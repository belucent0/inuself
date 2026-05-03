/* No-op stub for librocprofiler-sdk.so.
 *
 * Background: torch 2.10 (in rocm/vllm-dev:nightly) directly links 18 rocprofiler_*
 * symbols. The real librocprofiler-sdk crashes with FATAL at HSA init in WSL because
 * /sys/class/kfd/kfd/topology/nodes is missing.
 *
 * This stub provides every symbol torch+register expect, all as no-ops returning
 * SUCCESS (=0) or NULL. Result:
 *   - dynamic linker resolves all symbols at load time
 *   - librocprofiler-register dlopens us, calls rocprofiler_configure → NULL → no tool registers
 *   - torch's tracing calls (create_context, iterate_*) all no-op silently
 *   - HSA init proceeds; no FATAL agent reconciliation
 */
#include <stddef.h>
#include <stdint.h>

#define API __attribute__((visibility("default")))

typedef int rocprofiler_status_t;
typedef struct { uint64_t handle; } rocprofiler_context_id_t;
typedef struct { uint64_t handle; } rocprofiler_buffer_id_t;

typedef struct rocprofiler_tool_configure_result_t {
    size_t size;
    void* initialize;
    void* finalize;
    void* tool_data;
} rocprofiler_tool_configure_result_t;

API rocprofiler_tool_configure_result_t* rocprofiler_configure(
    unsigned int version, const char* runtime_version,
    unsigned int priority, void* id) {
    (void)version; (void)runtime_version; (void)priority; (void)id;
    return NULL;
}

API int rocprofiler_force_configure(void* config_func) {
    (void)config_func; return 0;
}

API rocprofiler_status_t rocprofiler_configure_buffer_tracing_service(
    rocprofiler_context_id_t ctx, int kind, const int* ops, size_t n,
    rocprofiler_buffer_id_t buf) {
    (void)ctx; (void)kind; (void)ops; (void)n; (void)buf; return 0;
}

API rocprofiler_status_t rocprofiler_configure_callback_tracing_service(
    rocprofiler_context_id_t ctx, int kind, const int* ops, size_t n,
    void* cb, void* data) {
    (void)ctx; (void)kind; (void)ops; (void)n; (void)cb; (void)data; return 0;
}

API rocprofiler_status_t rocprofiler_context_is_valid(
    rocprofiler_context_id_t ctx, int* out) {
    (void)ctx; if (out) *out = 0; return 0;
}

API rocprofiler_status_t rocprofiler_create_buffer(
    rocprofiler_context_id_t ctx, size_t size, size_t watermark,
    int policy, void* cb, void* data, rocprofiler_buffer_id_t* out) {
    (void)ctx; (void)size; (void)watermark; (void)policy;
    (void)cb; (void)data;
    if (out) out->handle = 0;
    return 0;
}

API rocprofiler_status_t rocprofiler_create_context(rocprofiler_context_id_t* out) {
    if (out) out->handle = 0;
    return 0;
}

API rocprofiler_status_t rocprofiler_flush_buffer(rocprofiler_buffer_id_t buf) {
    (void)buf; return 0;
}

API rocprofiler_status_t rocprofiler_iterate_buffer_tracing_kind_operations(
    int kind, void* cb, void* data) {
    (void)kind; (void)cb; (void)data; return 0;
}

API rocprofiler_status_t rocprofiler_iterate_buffer_tracing_kinds(
    void* cb, void* data) {
    (void)cb; (void)data; return 0;
}

API rocprofiler_status_t rocprofiler_iterate_callback_tracing_kind_operation_args(
    int record, void* cb, int max_depth, void* data) {
    (void)record; (void)cb; (void)max_depth; (void)data; return 0;
}

API rocprofiler_status_t rocprofiler_iterate_callback_tracing_kind_operations(
    int kind, void* cb, void* data) {
    (void)kind; (void)cb; (void)data; return 0;
}

API rocprofiler_status_t rocprofiler_iterate_callback_tracing_kinds(
    void* cb, void* data) {
    (void)cb; (void)data; return 0;
}

API rocprofiler_status_t rocprofiler_query_available_agents(
    int version, void* cb, size_t agent_size, void* data) {
    (void)version; (void)cb; (void)agent_size; (void)data; return 0;
}

API rocprofiler_status_t rocprofiler_query_buffer_tracing_kind_name(
    int kind, const char** out, uint64_t* len) {
    (void)kind; if (out) *out = ""; if (len) *len = 0; return 0;
}

API rocprofiler_status_t rocprofiler_query_buffer_tracing_kind_operation_name(
    int kind, int op, const char** out, uint64_t* len) {
    (void)kind; (void)op; if (out) *out = ""; if (len) *len = 0; return 0;
}

API rocprofiler_status_t rocprofiler_query_callback_tracing_kind_name(
    int kind, const char** out, uint64_t* len) {
    (void)kind; if (out) *out = ""; if (len) *len = 0; return 0;
}

API rocprofiler_status_t rocprofiler_query_callback_tracing_kind_operation_name(
    int kind, int op, const char** out, uint64_t* len) {
    (void)kind; (void)op; if (out) *out = ""; if (len) *len = 0; return 0;
}

API rocprofiler_status_t rocprofiler_start_context(rocprofiler_context_id_t ctx) {
    (void)ctx; return 0;
}

API rocprofiler_status_t rocprofiler_stop_context(rocprofiler_context_id_t ctx) {
    (void)ctx; return 0;
}

/* Called by librocprofiler-register at HSA init to register API tables.
 * Returning 0 (success) without doing anything is sufficient for register
 * to skip rocprofiler tool integration entirely.
 */
API rocprofiler_status_t rocprofiler_set_api_table(
    const char* name, uint64_t lib_version, uint64_t lib_instance,
    void** tables, uint64_t num_tables) {
    (void)name; (void)lib_version; (void)lib_instance;
    (void)tables; (void)num_tables;
    return 0;
}
