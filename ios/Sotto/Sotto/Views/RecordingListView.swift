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
