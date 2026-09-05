#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>

/*
 * リポジトリの場所は .app の中からは辿れないため、ビルド時に
 * -DVIT_WORK_DIR="\"/path/to/repo\"" で埋め込む（packaging/build_app.sh が渡す）。
 * 埋め込みが無い場合や .app だけを別マシンへ持ってきた場合に備えて、
 * 環境変数と既定の配置場所も順に見る。
 */
#ifndef VIT_WORK_DIR
#define VIT_WORK_DIR ""
#endif

static char work_dir[PATH_MAX];

static int looks_like_work_dir(const char *path) {
    if (path == NULL || path[0] == '\0') {
        return 0;
    }
    char script[PATH_MAX];
    if (snprintf(script, sizeof(script), "%s/voice_input.py", path) >= (int)sizeof(script)) {
        return 0;
    }
    return access(script, R_OK) == 0;
}

static void resolve_work_dir(void) {
    char fallback[PATH_MAX];
    const char *home = getenv("HOME");
    snprintf(fallback, sizeof(fallback), "%s/voice-input-tool", home ? home : "");

    const char *candidates[] = {
        getenv("VOICE_INPUT_TOOL_DIR"),  /* 開発中に別のチェックアウトを指したいとき */
        VIT_WORK_DIR,                    /* ビルドしたマシンでのリポジトリの場所 */
        fallback,                        /* READMEの既定の配置 */
    };

    for (size_t i = 0; i < sizeof(candidates) / sizeof(candidates[0]); i++) {
        if (looks_like_work_dir(candidates[i])) {
            snprintf(work_dir, sizeof(work_dir), "%s", candidates[i]);
            return;
        }
    }
    /* どれも見つからない場合でもログ出力先などは必要なので既定値を使う */
    snprintf(work_dir, sizeof(work_dir), "%s", fallback);
}

static void work_path(char *out, size_t size, const char *relative) {
    snprintf(out, size, "%s/%s", work_dir, relative);
}

int main(void) {
    resolve_work_dir();
    chdir(work_dir);

    char log_path[PATH_MAX];
    char error_log_path[PATH_MAX];
    char python_path[PATH_MAX];
    char script_path[PATH_MAX];
    work_path(log_path, sizeof(log_path), "logs/app-launcher.log");
    work_path(error_log_path, sizeof(error_log_path), "logs/app-launcher-error.log");
    work_path(python_path, sizeof(python_path), ".venv-framework/bin/python3");
    work_path(script_path, sizeof(script_path), "voice_input.py");

    FILE *launcher_log = fopen(log_path, "a");
    if (launcher_log) {
        time_t now = time(NULL);
        fprintf(launcher_log, "launcher started at %ld (work dir: %s)\n", (long)now, work_dir);
        fflush(launcher_log);
        fclose(launcher_log);
    }

    freopen(log_path, "a", stdout);
    freopen(error_log_path, "a", stderr);

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
