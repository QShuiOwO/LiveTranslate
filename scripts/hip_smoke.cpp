#include <hip/hip_runtime.h>
#include <cstdio>
#include <cstring>

__global__ void add_one(int *values) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < 1024) values[i] += 1;
}

int main() {
    int count = 0;
    hipError_t rc = hipGetDeviceCount(&count);
    if (rc != hipSuccess) { std::fprintf(stderr, "hipGetDeviceCount: %s\n", hipGetErrorString(rc)); return 1; }
    int selected = -1;
    hipDeviceProp_t props{};
    for (int i = 0; i < count; ++i) {
        if (hipGetDeviceProperties(&props, i) != hipSuccess) return 2;
        if (std::strncmp(props.gcnArchName, "gfx1201", 7) == 0) { selected = i; break; }
    }
    if (selected < 0) { std::fprintf(stderr, "No gfx1201 device found\n"); return 3; }
    if (hipSetDevice(selected) != hipSuccess) return 4;
    int host[1024]{};
    int *device = nullptr;
    if ((rc = hipMalloc(&device, sizeof(host))) != hipSuccess) {
        std::fprintf(stderr, "hipMalloc: %s\n", hipGetErrorString(rc)); return 5;
    }
    if ((rc = hipMemcpy(device, host, sizeof(host), hipMemcpyHostToDevice)) == hipSuccess) {
        hipLaunchKernelGGL(add_one, dim3(4), dim3(256), 0, 0, device);
        rc = hipGetLastError();
    }
    if (rc == hipSuccess) rc = hipDeviceSynchronize();
    if (rc == hipSuccess) rc = hipMemcpy(host, device, sizeof(host), hipMemcpyDeviceToHost);
    hipFree(device);
    if (rc != hipSuccess) { std::fprintf(stderr, "HIP compute: %s\n", hipGetErrorString(rc)); return 6; }
    for (int value : host) if (value != 1) return 7;
    std::printf("HIP kernel verified: %s (%s), 1024/1024 values correct\n", props.name, props.gcnArchName);
    return 0;
}
