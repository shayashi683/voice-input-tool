#import "VITNativeSupport.h"
#include <unistd.h>

static NSString *VITTrimmedFileContent(NSString *path) {
    if (![path length]) {
        return @"";
    }

    NSString *content = [NSString stringWithContentsOfFile:path
                                                  encoding:NSUTF8StringEncoding
                                                     error:nil];
    if (![content isKindOfClass:[NSString class]]) {
        return @"";
    }
    return [content stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
}

NSString *VITResolvedWorkDir(void) {
    static NSString *workDir = nil;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        NSString *envDir = [[[NSProcessInfo processInfo] environment] objectForKey:@"VOICE_INPUT_TOOL_DIR"];
        if ([envDir isKindOfClass:[NSString class]] && [envDir length] > 0) {
            workDir = [[envDir stringByExpandingTildeInPath] copy];
            return;
        }

        NSString *resourcePath = [[NSBundle mainBundle] pathForResource:@"workdir" ofType:@"txt"];
        NSString *resourceDir = VITTrimmedFileContent(resourcePath);
        if ([resourceDir length] > 0) {
            workDir = [[resourceDir stringByExpandingTildeInPath] copy];
            return;
        }
        workDir = [[@"~/voice-input-tool" stringByExpandingTildeInPath] copy];
    });
    return workDir;
}

NSString *VITWorkDirPath(NSString *relativePath) {
    return [VITResolvedWorkDir() stringByAppendingPathComponent:relativePath];
}

NSString *VITPythonPath(void) { return VITWorkDirPath(@".venv-framework/bin/python3"); }
NSString *VITScriptPath(void) { return VITWorkDirPath(@"voice_input.py"); }
NSString *VITCommandFilePath(void) { return VITWorkDirPath(@"logs/voice-input-command.txt"); }
NSString *VITOutputFilePath(void) { return VITWorkDirPath(@"logs/voice-input-output.jsonl"); }
NSString *VITStatusFilePath(void) { return VITWorkDirPath(@"logs/voice-input-status.json"); }
NSString *VITPasteReadyPath(void) { return VITWorkDirPath(@"logs/native-paste-ready.txt"); }

NSMutableDictionary *VITTaskEnvironment(void) {
    NSMutableDictionary *environment = [[[NSProcessInfo processInfo] environment] mutableCopy];
    [environment setObject:VITResolvedWorkDir() forKey:@"VOICE_INPUT_TOOL_DIR"];
    return environment;
}

void VITEnsureParentDirectory(NSString *path) {
    NSString *directory = [path stringByDeletingLastPathComponent];
    [[NSFileManager defaultManager] createDirectoryAtPath:directory
                              withIntermediateDirectories:YES
                                               attributes:nil
                                                    error:nil];
}

void VITNativeLog(NSString *message) {
    NSString *path = VITWorkDirPath(@"logs/native-status.log");
    VITEnsureParentDirectory(path);
    NSString *line = [NSString stringWithFormat:@"%@ %@\n", [NSDate date], message];
    NSFileHandle *handle = [NSFileHandle fileHandleForWritingAtPath:path];
    if (!handle) {
        [[NSFileManager defaultManager] createFileAtPath:path contents:nil attributes:nil];
        handle = [NSFileHandle fileHandleForWritingAtPath:path];
    }
    if (!handle) {
        return;
    }
    [handle seekToEndOfFile];
    [handle writeData:[line dataUsingEncoding:NSUTF8StringEncoding]];
    [handle closeFile];
}

void VITOpenAccessibilitySettings(void) {
    NSArray<NSString *> *urlStrings = @[
        @"x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_Accessibility",
        @"x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
    ];
    for (NSString *urlString in urlStrings) {
        NSURL *url = [NSURL URLWithString:urlString];
        if (url && [[NSWorkspace sharedWorkspace] openURL:url]) {
            return;
        }
    }

    NSURL *settingsURL = [NSURL fileURLWithPath:@"/System/Applications/System Settings.app"];
    if (![[NSWorkspace sharedWorkspace] openURL:settingsURL]) {
        VITNativeLog(@"failed to open accessibility settings");
    }
}

pid_t VITCurrentFrontmostApplicationPid(void) {
    NSRunningApplication *frontmost = [[NSWorkspace sharedWorkspace] frontmostApplication];
    if (!frontmost) {
        return 0;
    }
    pid_t pid = [frontmost processIdentifier];
    return pid == getpid() ? 0 : pid;
}

NSImage *VITStatusBarIcon(void) {
    static NSImage *icon = nil;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        NSString *path = [[NSBundle mainBundle] pathForResource:@"StatusIconTemplate" ofType:@"png"];
        if ([path length] > 0) {
            icon = [[NSImage alloc] initWithContentsOfFile:path];
            [icon setTemplate:YES];
            [icon setSize:NSMakeSize(18, 18)];
        }
    });
    return icon;
}
