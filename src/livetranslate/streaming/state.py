from collections import OrderedDict, deque
import re
import threading
import unicodedata

from ..languages import text_units


def stable_prefix(previous, current, language="en"):
    if language == "ja":
        old, new = (unicodedata.normalize("NFC", text) for text in (previous, current))
        count = 0
        for a, b in zip(old, new):
            if a != b: break
            count += 1
        return new[:count].rstrip()
    old, new = (unicodedata.normalize("NFC", text).split() for text in (previous, current))
    punctuation = ",.!?;:" if language == "en" else ",.!?;:…¿¡«»“”„\""
    count = 0
    for a, b in zip(old, new):
        if a.casefold().strip(punctuation) != b.casefold().strip(punctuation): break
        count += 1
    return " ".join(new[:count])


def complete_clause(text, language="en"):
    # Only stable, substantial clauses trigger a provisional translation.
    if language == "ja":
        matches = list(re.finditer(r"[。！？!?、]", text))
        if matches:
            clause = text[:matches[-1].end()].strip()
            if text_units(clause, language) >= 6: return clause
        return text.strip() if text_units(text, language) >= 16 else ""
    pattern = r"[,.!?;:](?:\s|$)" if language == "en" else r"[,.!?;:…][»”“’\"']*(?:\s|$)"
    matches = list(re.finditer(pattern, text))
    minimum_clause = 5 if language == "en" else (3 if language == "ko" else 4)
    if matches:
        clause = text[:matches[-1].end()].strip()
        if len(clause.split()) >= minimum_clause: return clause
    words = text.split()
    while language == "en" and words and words[-1].lower().strip(",.!?;:") in {"a", "an", "the", "and", "or", "but", "of", "to", "for", "with", "in", "on", "at"}:
        words.pop()
    return " ".join(words) if len(words) >= (4 if language == "ko" else 6) else ""


def is_prefix(prefix, text, language="en"):
    if language == "ja":
        return bool(prefix) and unicodedata.normalize("NFC", text).startswith(unicodedata.normalize("NFC", prefix))
    return bool(prefix) and len(stable_prefix(prefix, text, language).split()) == len(prefix.split())


class CaptionBook:
    def __init__(self, limit=80):
        self.limit = limit
        self.rows = OrderedDict()
        self.lock = threading.RLock()
        self.ignored_stale = 0
        self.final_mutation_attempts = 0
        self.final_count = 0
        self.bilingual_count = 0
        self.highest_final_uid = 0

    def clear(self):
        with self.lock:
            self.rows.clear()

    def asr(self, job, text):
        with self.lock:
            row = self.rows.get(job.uid)
            if job.uid <= self.highest_final_uid:
                if row and job.final and row["text"] != text: self.final_mutation_attempts += 1
                self.ignored_stale += 1
                return None
            if row and (row["final"] or row["revision"] >= job.revision):
                self.ignored_stale += 1
                return None
            stable = stable_prefix(row["text"], text, job.source_language) if row else ""
            translated_source = row.get("translated_source", "") if row else ""
            keep_translation = bool(row and is_prefix(translated_source, text, job.source_language))
            row = {"uid": job.uid, "revision": job.revision, "text": text,
                   "source_language": job.source_language, "language_generation": job.language_generation,
                   "stable": stable, "translated": row["translated"] if keep_translation else "",
                   "translated_source": translated_source if keep_translation else "",
                   "final": job.final, "zh_final": False, "error": None,
                   "onset_ns": job.onset_ns, "speech_end_ns": job.speech_end_ns,
                   "audio_end_ns": job.audio_end_ns, "reason": job.reason,
                   "seen": row["seen"] if row else {}}
            self.rows[job.uid] = row
            while len(self.rows) > self.limit + 4: self.rows.popitem(last=False)
            if job.final:
                self.final_count += 1
                self.highest_final_uid = job.uid
            return {**row, "seen": None}

    def translation(self, job, text):
        with self.lock:
            row = self.rows.get(job.uid)
            if row is None or row["zh_final"]:
                self.ignored_stale += 1
                return None
            if job.final:
                if not row["final"] or job.text != row["text"]:
                    self.ignored_stale += 1
                    return None
                row["translated"], row["zh_final"] = text, True
                row["translated_source"] = job.text
                self.bilingual_count += 1
            elif not row["final"] and is_prefix(job.text, row["text"], job.speech.source_language):
                row["translated"] = text
                row["translated_source"] = job.text
            else:
                self.ignored_stale += 1
                return None
            return {**row, "seen": None}

    def failure(self, uid, message):
        with self.lock:
            row = self.rows.get(uid)
            if not row: return None
            row["error"] = message
            return {**row, "seen": None}

    def presented(self, snapshot, field, now_ns):
        """Called after the real label paint; returns new latency observations only once."""
        with self.lock:
            row = self.rows.get(snapshot["uid"])
            if not row: return {}
            seen, metrics = row["seen"], {}
            key = f"first_{field}"
            if key not in seen:
                seen[key] = now_ns
                metrics[key + "_ms"] = (now_ns - snapshot["onset_ns"]) / 1e6
            is_final = snapshot["final"] if field == "en" else snapshot["zh_final"]
            if is_final and f"final_{field}" not in seen:
                seen[f"final_{field}"] = now_ns
            if "final_en" in seen and "final_zh" in seen and "final_both" not in seen:
                seen["final_both"] = max(seen["final_en"], seen["final_zh"])
                metrics["final_from_onset_ms"] = (seen["final_both"] - row["onset_ns"]) / 1e6
                metrics["final_from_speech_end_ms"] = (seen["final_both"] - row["speech_end_ns"]) / 1e6
            return metrics


class EventBus:
    def __init__(self, capacity=256):
        self.capacity = capacity
        self.items = OrderedDict()
        self.lock = threading.Lock()
        self.dropped = 0
        self.high_water = 0

    def put(self, event):
        key = (event["kind"], event.get("uid", 0))
        with self.lock:
            self.items[key] = event
            if len(self.items) > self.capacity:
                self.items.popitem(last=False); self.dropped += 1
            self.high_water = max(self.high_water, len(self.items))

    def drain(self):
        with self.lock:
            result = list(self.items.values()); self.items.clear()
            return result
