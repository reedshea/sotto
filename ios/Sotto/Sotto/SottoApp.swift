import SwiftUI

@main
struct SottoApp: App {
    @StateObject private var recordingStore = RecordingStore()

    var body: some Scene {
        WindowGroup {
            RecordingListView()
                .environmentObject(recordingStore)
        }
    }
}
