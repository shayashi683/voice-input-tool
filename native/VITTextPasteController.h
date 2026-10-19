#import <Cocoa/Cocoa.h>

@interface VITTextPasteController : NSObject
- (instancetype)initWithStatusTitleHandler:(void (^)(NSString *title))handler;
- (void)pasteText:(NSString *)text targetPid:(pid_t)pid expiresAt:(NSTimeInterval)deadline completion:(void (^)(NSString *status))completion;
@end
