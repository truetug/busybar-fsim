/* Модули HTTP API, не собранные в fsim (web_server !exclude= в services.txt): железо, облако,
   обновления. Пути зарегистрированы, ответ — 501 с объяснением. */
#include <furi.h>
#include <web_server/http_api/http_api.h>

static bool fsim_api_unavailable(struct mg_connection* conn, const char* module) {
    mg_http_reply(conn, 501, "Content-Type: application/json\r\n",
        "{\"error\":\"%s is not available in fsim\"}\n", module);
    return true;
}

void* http_api_audio_alloc(void) {
    return NULL;
}

void http_api_audio_free(void* ctx) {
    UNUSED(ctx);
}

bool http_api_audio_callback(FuriString* path, HttpMethod method, struct mg_connection* conn, struct mg_http_message* msg, void* ctx) {
    UNUSED(path);
    UNUSED(method);
    UNUSED(msg);
    UNUSED(ctx);
    return fsim_api_unavailable(conn, "audio");
}

bool http_api_status_callback(FuriString* path, HttpMethod method, struct mg_connection* conn, struct mg_http_message* msg, void* ctx) {
    UNUSED(path);
    UNUSED(method);
    UNUSED(msg);
    UNUSED(ctx);
    return fsim_api_unavailable(conn, "status");
}

void* http_api_status_alloc(void) {
    return NULL;
}

void http_api_status_free(void* ctx) {
    UNUSED(ctx);
}

void* http_api_status_ws_alloc(void) {
    return NULL;
}

void http_api_status_ws_free(void* ctx) {
    UNUSED(ctx);
}

bool http_api_status_ws_callback(FuriString* path, HttpMethod method, struct mg_connection* conn, struct mg_http_message* msg, void* ctx) {
    UNUSED(path);
    UNUSED(method);
    UNUSED(msg);
    UNUSED(ctx);
    return fsim_api_unavailable(conn, "status");
}

void* http_api_wifi_alloc(void) {
    return NULL;
}

void http_api_wifi_free(void* ctx) {
    UNUSED(ctx);
}

bool http_api_wifi_callback(FuriString* path, HttpMethod method, struct mg_connection* conn, struct mg_http_message* msg, void* ctx) {
    UNUSED(path);
    UNUSED(method);
    UNUSED(msg);
    UNUSED(ctx);
    return fsim_api_unavailable(conn, "wifi");
}

void* http_api_update_alloc(void) {
    return NULL;
}

void http_api_update_free(void* ctx) {
    UNUSED(ctx);
}

bool http_api_update_callback(FuriString* path, HttpMethod method, struct mg_connection* conn, struct mg_http_message* msg, void* ctx) {
    UNUSED(path);
    UNUSED(method);
    UNUSED(msg);
    UNUSED(ctx);
    return fsim_api_unavailable(conn, "update");
}

bool http_api_update_hdr_callback_root(FuriString* path, HttpMethod method, struct mg_connection* conn, struct mg_http_message* msg, void* ctx) {
    UNUSED(path);
    UNUSED(method);
    UNUSED(msg);
    UNUSED(ctx);
    return fsim_api_unavailable(conn, "update");
}

void* http_api_ble_alloc(void) {
    return NULL;
}

void http_api_ble_free(void* ctx) {
    UNUSED(ctx);
}

bool http_api_ble_callback(FuriString* path, HttpMethod method, struct mg_connection* conn, struct mg_http_message* msg, void* ctx) {
    UNUSED(path);
    UNUSED(method);
    UNUSED(msg);
    UNUSED(ctx);
    return fsim_api_unavailable(conn, "ble");
}

bool http_api_log_dump_callback(FuriString* path, HttpMethod method, struct mg_connection* conn, struct mg_http_message* msg, void* ctx) {
    UNUSED(path);
    UNUSED(method);
    UNUSED(msg);
    UNUSED(ctx);
    return fsim_api_unavailable(conn, "log_dump");
}

void* http_api_account_alloc(void) {
    return NULL;
}

void http_api_account_free(void* ctx) {
    UNUSED(ctx);
}

bool http_api_account_callback(FuriString* path, HttpMethod method, struct mg_connection* conn, struct mg_http_message* msg, void* ctx) {
    UNUSED(path);
    UNUSED(method);
    UNUSED(msg);
    UNUSED(ctx);
    return fsim_api_unavailable(conn, "account");
}

void* http_api_busy_alloc(void) {
    return NULL;
}

void http_api_busy_free(void* ctx) {
    UNUSED(ctx);
}

bool http_api_busy_callback(FuriString* path, HttpMethod method, struct mg_connection* conn, struct mg_http_message* msg, void* ctx) {
    UNUSED(path);
    UNUSED(method);
    UNUSED(msg);
    UNUSED(ctx);
    return fsim_api_unavailable(conn, "busy");
}

void* http_api_smart_home_alloc(void) {
    return NULL;
}

void http_api_smart_home_free(void* ctx) {
    UNUSED(ctx);
}

bool http_api_smart_home_callback(FuriString* path, HttpMethod method, struct mg_connection* conn, struct mg_http_message* msg, void* ctx) {
    UNUSED(path);
    UNUSED(method);
    UNUSED(msg);
    UNUSED(ctx);
    return fsim_api_unavailable(conn, "smart_home");
}
