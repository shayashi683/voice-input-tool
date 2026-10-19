// Exercise first-run state and the actual native guide without mic/network access.
#define main VoiceInputAppMain
#import "../native/native_status_app.m"
#undef main

@interface VITSetupGuideController (Testing)
- (void)dismissGuide:(id)sender;
- (void)completeGuide:(id)sender;
- (void)openAppSettings:(id)sender;
- (void)openAccessibilitySettings:(id)sender;
- (void)fitToVisibleFrame:(NSRect)visibleFrame;
@end

@interface StartupTestDelegate : AppDelegate
@property(nonatomic, assign) NSInteger permissionPrompts;
@property(nonatomic, assign) NSInteger guidesPresented;
@property(nonatomic, assign) NSInteger enginesStarted;
@end
@implementation StartupTestDelegate
- (void)requestAccessibilityIfNeeded { self.permissionPrompts += 1; }
- (void)setupStatusItem {}
- (void)registerConfiguredHotKey {}
- (void)prepareOutputPolling {}
- (void)startEngine { self.enginesStarted += 1; }
- (void)openSetupGuide:(id)sender { (void)sender; self.guidesPresented += 1; }
@end

static void AssertContainedViews(NSView *parent) {
    if ([parent isKindOfClass:NSScrollView.class]) return; // Document overflow is intentional.
    for (NSView *child in parent.subviews) {
        NSCAssert(NSContainsRect(parent.bounds, child.frame), @"All guide controls must be inside their parent: %@", child);
        AssertContainedViews(child);
    }
}

static void WriteJSON(NSDictionary *json, NSString *path) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:json options:0 error:nil];
    NSCAssert([data writeToFile:path options:NSDataWritingAtomic error:nil], @"Fixture write must succeed");
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        [NSApplication sharedApplication];
        NSString *directory = VITResolvedWorkDir();
        NSString *marker = [directory stringByAppendingPathComponent:@"setup-guide.json"];
        NSString *configPath = [directory stringByAppendingPathComponent:@"config.json"];
        NSFileManager *files = NSFileManager.defaultManager;
        [files createDirectoryAtPath:directory withIntermediateDirectories:YES attributes:nil error:nil];
        NSCAssert(VITSetupGuideNeedsPresentation(directory), @"A new installation/update shows the guide");

        StartupTestDelegate *firstLaunch = [StartupTestDelegate new];
        NSNotification *launch = [NSNotification notificationWithName:NSApplicationDidFinishLaunchingNotification object:NSApp];
        [firstLaunch applicationDidFinishLaunching:launch];
        NSCAssert(firstLaunch.guidesPresented == 1 && firstLaunch.permissionPrompts == 0,
                  @"Pending setup must show its guide before any accessibility prompt");
        NSCAssert(firstLaunch.enginesStarted == 1, @"Normal engine startup remains enabled");

        NSCAssert([VITSetupGuideHotKeyDisplay(directory) isEqualToString:@"Ctrl + Shift + Space"], @"Default hotkey must match recording default");
        WriteJSON(@{@"hotkey_record": @"<cmd>+g", @"input_device_id": @"name:Example"}, configPath);
        NSData *originalConfig = [NSData dataWithContentsOfFile:configPath];
        NSCAssert([VITSetupGuideHotKeyDisplay(directory) isEqualToString:@"Cmd + G"], @"Configured shortcut must be displayed");
        WriteJSON(@{@"hotkey_record": @"<right_shift>"}, configPath);
        originalConfig = [NSData dataWithContentsOfFile:configPath];
        NSCAssert([VITSetupGuideHotKeyDisplay(directory) isEqualToString:@"Right Shift"], @"Right Shift hotkey must be displayed");
        WriteJSON(@{@"hotkey_record": @"<left_shift>"}, configPath);
        originalConfig = [NSData dataWithContentsOfFile:configPath];
        NSCAssert([VITSetupGuideHotKeyDisplay(directory) isEqualToString:@"Left Shift"], @"Left Shift hotkey must be displayed");

        __block NSInteger settingsOpens = 0;
        __block NSInteger permissionsOpens = 0;
        VITSetupGuideController *guide = [[VITSetupGuideController alloc] initWithWorkDirectory:directory
            settingsHandler:^{ settingsOpens += 1; } accessibilityHandler:^{ permissionsOpens += 1; }];
        AssertContainedViews(guide.window.contentView);
        NSCAssert(guide.window.frame.size.height <= 740, @"Guide including its footer must fit a 13-inch Mac display");
        [guide openAppSettings:nil];
        [guide openAccessibilitySettings:nil];
        NSCAssert(settingsOpens == 1 && permissionsOpens == 1, @"Guide buttons route through the application's existing actions");
        [guide dismissGuide:nil];
        NSCAssert(VITSetupGuideNeedsPresentation(directory), @"Later never marks the guide complete");
        [guide.window performClose:nil];
        NSCAssert(VITSetupGuideNeedsPresentation(directory), @"Window close never marks the guide complete");
        [guide completeGuide:nil];
        NSCAssert(!VITSetupGuideNeedsPresentation(directory), @"Only explicit completion dismisses future startup guides");
        NSCAssert([originalConfig isEqual:[NSData dataWithContentsOfFile:configPath]], @"Guide must preserve the user's configuration");
        NSDictionary *attributes = [files attributesOfItemAtPath:marker error:nil];
        NSCAssert(([attributes[NSFilePosixPermissions] integerValue] & 0777) == 0600, @"Completion marker is private");

        StartupTestDelegate *laterLaunch = [StartupTestDelegate new];
        [laterLaunch applicationDidFinishLaunching:launch];
        NSCAssert(laterLaunch.guidesPresented == 0 && laterLaunch.permissionPrompts == 1,
                  @"Completed setup keeps the normal startup permission flow");
        VITSetupGuideController *reopened = [[VITSetupGuideController alloc] initWithWorkDirectory:directory
            settingsHandler:nil accessibilityHandler:nil];
        [reopened dismissGuide:nil];
        NSCAssert(!VITSetupGuideNeedsPresentation(directory), @"Manual reopening and closing preserve completion");

        for (id invalid in @[@{}, @{@"version": @0, @"completed": @YES}, @{@"version": @1, @"completed": @NO},
                             @{@"version": @"1", @"completed": @"yes"}]) {
            WriteJSON(invalid, marker);
            NSCAssert(VITSetupGuideNeedsPresentation(directory), @"Incomplete/invalid marker must show the guide");
        }
        [@"invalid json" writeToFile:marker atomically:YES encoding:NSUTF8StringEncoding error:nil];
        NSCAssert(VITSetupGuideNeedsPresentation(directory), @"Corrupt completion marker must be recoverable");
        NSString *blockedDirectory = [directory stringByAppendingPathComponent:@"not-a-directory"];
        [@"fixture" writeToFile:blockedDirectory atomically:YES encoding:NSUTF8StringEncoding error:nil];
        NSError *error = nil;
        NSCAssert(!VITMarkSetupGuideCompleted(blockedDirectory, &error) && error != nil, @"Failed completion writes must report an error");
        NSCAssert(VITSetupGuideNeedsPresentation(blockedDirectory), @"Failed completion writes leave setup pending");

        // Optional offscreen snapshot uses only the fixture work directory.
        if (argc == 2) {
            WriteJSON(@{}, configPath);
            [guide refreshConfiguredHotKey];
            guide.window.appearance = [NSAppearance appearanceNamed:NSAppearanceNameAqua];
            NSView *view = guide.window.contentView;
            [view displayIfNeeded];
            NSBitmapImageRep *image = [view bitmapImageRepForCachingDisplayInRect:view.bounds];
            [view cacheDisplayInRect:view.bounds toBitmapImageRep:image];
            NSData *png = [image representationUsingType:NSBitmapImageFileTypePNG properties:@{}];
            NSString *imagePath = [NSString stringWithUTF8String:argv[1]];
            NSCAssert([png writeToFile:imagePath atomically:YES], @"Guide snapshot must render");

        }
        [guide fitToVisibleFrame:NSMakeRect(0, 0, 1280, 680)];
        NSCAssert(guide.window.frame.size.height <= 660, @"Guide must fit a shorter usable display height");
        for (NSView *child in guide.window.contentView.subviews) {
            NSCAssert(NSContainsRect(guide.window.contentView.bounds, child.frame), @"Footer controls stay visible on smaller displays");
        }
        if (argc == 2) {
            NSView *view = guide.window.contentView;
            [view displayIfNeeded];
            NSBitmapImageRep *image = [view bitmapImageRepForCachingDisplayInRect:view.bounds];
            [view cacheDisplayInRect:view.bounds toBitmapImageRep:image];
            NSData *png = [image representationUsingType:NSBitmapImageFileTypePNG properties:@{}];
            NSString *imagePath = [NSString stringWithUTF8String:argv[1]];
            NSString *smallPath = [[imagePath stringByDeletingPathExtension] stringByAppendingString:@"-small.png"];
            NSCAssert([png writeToFile:smallPath atomically:YES], @"Small-display guide snapshot must render");
        }
        puts("Native setup: startup, explicit completion, close/later, failed writes, hotkeys, callbacks and layout passed");
    }
    return 0;
}
