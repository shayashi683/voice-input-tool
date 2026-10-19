#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <limits.h>

static void path_join(char *buffer, size_t buffer_size, const char *base, const char *relative) {
    snprintf(buffer, buffer_size, "%s/%s", base, relative);
}

int main(void) {
    const char *work_dir = getenv("VOICE_INPUT_TOOL_DIR");
    char default_work_dir[PATH_MAX];
    if (!work_dir || strlen(work_dir) == 0) {
        const char *home = getenv("HOME");
        if (!home || strlen(home) == 0) {
            home = ".";
        }
        path_join(default_work_dir, sizeof(default_work_dir), home, "voice-input-tool");
        work_dir = default_work_dir;
    }
    setenv("VOICE_INPUT_TOOL_DIR", work_dir, 0);
    chdir(work_dir);

    char launcher_log_path[PATH_MAX];
    char launcher_error_log_path[PATH_MAX];
    char python_path[PATH_MAX];
    char script_path[PATH_MAX];
    path_join(launcher_log_path, sizeof(launcher_log_path), work_dir, "logs/app-launcher.log");
    path_join(launcher_error_log_path, sizeof(launcher_error_log_path), work_dir, "logs/app-launcher-error.log");
    path_join(python_path, sizeof(python_path), work_dir, ".venv-framework/bin/python3");
    path_join(script_path, sizeof(script_path), work_dir, "voice_input.py");

    FILE *launcher_log = fopen(launcher_log_path, "a");
    if (launcher_log) {
        time_t now = time(NULL);
        fprintf(launcher_log, "launcher started at %ld\n", (long)now);
        fflush(launcher_log);
        fclose(launcher_log);
    }

    freopen(launcher_log_path, "a", stdout);
    freopen(launcher_error_log_path, "a", stderr);

    char *const args[] = {
        python_path,
        script_path,
        "--llm",
        NULL,
    };

    execv(args[0], args);
    perror("execv");
    return 1;
}
