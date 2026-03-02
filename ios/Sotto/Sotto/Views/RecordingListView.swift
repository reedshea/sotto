import SwiftUI

struct RecordingListView: View {
    @EnvironmentObject var store: RecordingStore
    @EnvironmentObject var destinationStore: DestinationStore
    @StateObject private var recorder = AudioRecorder()
    @State private var selectedPrivacy: PrivacyMode = .standard
    @State private var showingSettings = false

    var body: some View {
        NavigationStack {
            List {
                ForEach(store.recordings) { recording in
                    RecordingRow(recording: recording)
                }
            }
            .navigationTitle("Sotto")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showingSettings = true
                    } label: {
                        Image(systemName: "gearshape")
                    }
                }
                ToolbarItem(placement: .bottomBar) {
                    recordButton
                }
            }
            .sheet(isPresented: $showingSettings) {
                SettingsView()
            }
            .overlay {
                if store.recordings.isEmpty && !recorder.isRecording {
                    ContentUnavailableView(
                        "No Recordings",
                        systemImage: "waveform",
                        description: Text("Tap Record to capture your first voice note.")
                    )
                }
            }
        }
    }

    @ViewBuilder
    private var recordButton: some View {
        if recorder.isRecording {
            VStack(spacing: 8) {
                WaveformView(level: recorder.audioLevel)
                    .frame(height: 40)

                Text(formatTime(recorder.elapsedTime))
                    .font(.system(.title2, design: .monospaced))

                Button(action: stopRecording) {
                    Image(systemName: "stop.circle.fill")
                        .font(.system(size: 56))
                        .foregroundStyle(.red)
                }
            }
            .padding()
        } else {
            HStack(spacing: 24) {
                Button(action: { startRecording(privacy: .private) }) {
                    Label("Private", systemImage: "lock.fill")
                }

                Button(action: { startRecording(privacy: .standard) }) {
                    Label("Standard", systemImage: "waveform")
                }
            }
        }
    }

    private func startRecording(privacy: PrivacyMode) {
        selectedPrivacy = privacy
        _ = recorder.startRecording(privacy: privacy)
    }

    private func stopRecording() {
        let duration = recorder.stopRecording()
        var recording = Recording(
            id: UUID(),
            capturedAt: Date(),
            duration: duration,
            privacyMode: selectedPrivacy,
            status: .savedLocally,
            localFileURL: recorder.currentFileURL
        )

        if let destination = destinationStore.destination {
            recording.status = .uploading
            store.add(recording)
            UploadManager.shared.upload(recording: recording, destination: destination)
        } else {
            store.add(recording)
        }
    }

    private func formatTime(_ time: TimeInterval) -> String {
        let minutes = Int(time) / 60
        let seconds = Int(time) % 60
        let tenths = Int((time.truncatingRemainder(dividingBy: 1)) * 10)
        return String(format: "%02d:%02d.%d", minutes, seconds, tenths)
    }
}

// MARK: - Previews

#Preview("Empty state") {
    RecordingListView()
        .environmentObject(RecordingStore())
        .environmentObject(DestinationStore())
}

#Preview("With recordings") {
    let store = RecordingStore()
    let sampleRecordings: [Recording] = [
        Recording(
            id: UUID(),
            capturedAt: Date(),
            duration: 185,
            privacyMode: .standard,
            status: .done,
            title: "Weekly standup notes",
            summary: "Discussed sprint progress and blockers. Team agreed to push the release to next Friday."
        ),
        Recording(
            id: UUID(),
            capturedAt: Date().addingTimeInterval(-3600),
            duration: 42,
            privacyMode: .private,
            status: .transcribing,
            title: "Voice memo"
        ),
        Recording(
            id: UUID(),
            capturedAt: Date().addingTimeInterval(-7200),
            duration: 310,
            privacyMode: .standard,
            status: .uploading
        ),
        Recording(
            id: UUID(),
            capturedAt: Date().addingTimeInterval(-86400),
            duration: 67,
            privacyMode: .standard,
            status: .uploadFailed
        ),
        Recording(
            id: UUID(),
            capturedAt: Date().addingTimeInterval(-172800),
            duration: 540,
            privacyMode: .private,
            status: .done,
            title: "Client call — Acme Corp",
            summary: "Reviewed contract terms. They want to move forward with the enterprise plan."
        ),
    ]
    for r in sampleRecordings { store.add(r) }

    return RecordingListView()
        .environmentObject(store)
        .environmentObject(DestinationStore())
}
