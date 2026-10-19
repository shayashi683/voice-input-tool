// Explicit opt-in network smoke check. Downloads and verifies; never installs or quits apps.
#import "../native/VITUpdateController.m"

int main(void) {
    @autoreleasepool {
        __block BOOL done = NO;
        __block int result = 1;
        __block VITUpdateTransfer *packageTransfer = nil;
        VITUpdateTransfer *feedTransfer = [VITUpdateTransfer new];
        [feedTransfer startURL:[NSURL URLWithString:@"https://voice-input-tool.pages.dev/update.json"] limit:65536
            completion:^(NSData *data, NSError *error) {
            NSDictionary *release = error ? nil : VITValidateUpdateManifest(data, &error);
            if (!release) { NSLog(@"Update feed failed: %@", error); done = YES; return; }
            packageTransfer = [VITUpdateTransfer new];
            [packageTransfer startURL:[NSURL URLWithString:release[@"url"]] limit:[release[@"size"] unsignedIntegerValue]
                completion:^(NSData *package, NSError *failure) {
                if (!failure && VITVerifyUpdatePackage(package, release)) {
                    printf("Live updater verified version %s, %lu bytes; no installation performed\n",
                           [release[@"version"] UTF8String], (unsigned long)package.length);
                    result = 0;
                } else { NSLog(@"Package verification failed: %@", failure); }
                done = YES;
            }];
        }];
        NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:150];
        while (!done && deadline.timeIntervalSinceNow > 0)
            [NSRunLoop.currentRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.05]];
        [feedTransfer cancel]; [packageTransfer cancel];
        return result;
    }
}
