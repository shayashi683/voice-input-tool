#import <Cocoa/Cocoa.h>
#include <sys/types.h>

FOUNDATION_EXPORT NSString *VITResolvedWorkDir(void);
FOUNDATION_EXPORT NSString *VITWorkDirPath(NSString *relativePath);
FOUNDATION_EXPORT NSString *VITPythonPath(void);
FOUNDATION_EXPORT NSString *VITScriptPath(void);
FOUNDATION_EXPORT NSString *VITCommandFilePath(void);
FOUNDATION_EXPORT NSString *VITOutputFilePath(void);
FOUNDATION_EXPORT NSString *VITStatusFilePath(void);
FOUNDATION_EXPORT NSString *VITPasteReadyPath(void);
FOUNDATION_EXPORT NSMutableDictionary *VITTaskEnvironment(void);
FOUNDATION_EXPORT void VITEnsureParentDirectory(NSString *path);
FOUNDATION_EXPORT void VITNativeLog(NSString *message);
FOUNDATION_EXPORT void VITOpenAccessibilitySettings(void);
FOUNDATION_EXPORT pid_t VITCurrentFrontmostApplicationPid(void);
FOUNDATION_EXPORT NSImage *VITStatusBarIcon(void);
