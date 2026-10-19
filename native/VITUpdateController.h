#import <Cocoa/Cocoa.h>

FOUNDATION_EXPORT BOOL VITUpdateVersionIsValid(id version);
FOUNDATION_EXPORT NSComparisonResult VITCompareUpdateVersions(NSString *left, NSString *right);
FOUNDATION_EXPORT NSDictionary *VITValidateUpdateManifest(NSData *data, NSError **error);
FOUNDATION_EXPORT BOOL VITVerifyUpdatePackage(NSData *data, NSDictionary *release);

@interface VITUpdateController : NSWindowController <NSWindowDelegate>
- (instancetype)initWithCurrentVersion:(NSString *)version
                    canInstallHandler:(BOOL (^)(void))canInstall
                       installHandler:(BOOL (^)(NSURL *packageURL))install;
- (void)showUpdater;
- (void)installationFailed:(NSString *)message;
@end
