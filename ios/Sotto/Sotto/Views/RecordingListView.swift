import Combine
import SwiftUI

struct RecordingListView: View {
    @EnvironmentObject var store: RecordingStore
    @EnvironmentObject var destinationStore: DestinationStore
    @EnvironmentObject var poller: JobPoller
    @StateObject private var recorder = AudioRecorder()
    @State private var selectedPrivacy: PrivacyMode = .private
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
            .safeAreaInset(edge: .bottom) {
                recordPanel
                    .padding(.bottom, 16)
                    .frame(maxWidth: .infinity)
                    .background(alignment: .bottom) {
                        Color(.systemBackground)
                            .ignoresSafeArea(edges: .bottom)
                    }
                    .background(alignment: .top) {
                        LinearGradient(
                            colors: [
                                Color(.systemBackground).opacity(0),
                                Color(.systemBackground),
                            ],
                            startPoint: .top,
                            endPoint: .bottom
                        )
                        .frame(height: 20)
                        .offset(y: -20)
                    }
            }
        }
    }

    // MARK: - Record Panel

    @ViewBuilder
    private var recordPanel: some View {
        if recorder.isRecording {
            // --- Recording tray ---
            VStack(spacing: 16) {
                WaveformView(level: recorder.audioLevel)
                    .frame(height: 48)
                    .padding(.horizontal, 32)

                Text(formatTime(recorder.elapsedTime))
                    .font(.system(size: 34, weight: .light, design: .monospaced))

                Button(action: stopRecording) {
                    ZStack {
                        Circle()
                            .fill(.red)
                            .frame(width: 72, height: 72)
                        RoundedRectangle(cornerRadius: 6)
                            .fill(.white)
                            .frame(width: 26, height: 26)
                    }
                }
                .padding(.bottom, 8)
            }
            .padding(.horizontal)
            .padding(.bottom, 8)
            .transition(.move(edge: .bottom).combined(with: .opacity))
        } else {
            // --- Idle: toggle + record button ---
            VStack(spacing: 20) {
                Picker("Privacy", selection: $selectedPrivacy) {
                    Label("Private", systemImage: "lock.fill")
                        .tag(PrivacyMode.private)
                    Label("Standard", systemImage: "waveform")
                        .tag(PrivacyMode.standard)
                }
                .pickerStyle(.segmented)
                .frame(width: 220)

                Button(action: { startRecording(privacy: selectedPrivacy) }) {
                    ZStack {
                        Circle()
                            .stroke(.red.opacity(0.3), lineWidth: 4)
                            .frame(width: 80, height: 80)
                        Circle()
                            .fill(.red)
                            .frame(width: 64, height: 64)
                    }
                }
            }
            .padding(.top, 24)
            .padding(.bottom, 16)
            .transition(.move(edge: .bottom).combined(with: .opacity))
        }
    }

    private func startRecording(privacy: PrivacyMode) {
        selectedPrivacy = privacy
        withAnimation(.easeInOut(duration: 0.3)) {
            _ = recorder.startRecording(privacy: privacy)
        }
    }

    private func stopRecording() {
        let duration = withAnimation(.easeInOut(duration: 0.3)) {
            recorder.stopRecording()
        }
        var recording = Recording(
            id: UUID(),
            capturedAt: Date(),
            duration: duration,
            privacyMode: selectedPrivacy,
            status: .savedLocally,
            localFileURL: recorder.currentFileURL
        )

        guard let destination = destinationStore.destination else {
            store.add(recording)
            return
        }

        recording.status = .uploading
        store.add(recording)

        Task {
            do {
                let resp = try await UploadManager.shared.upload(
                    recording: recording, destination: destination
                )
                var updated = recording
                updated.serverUUID = resp.uuid
                updated.status = .uploaded
                store.update(updated)
                poller.startIfNeeded()
            } catch {
                var updated = recording
                updated.status = .uploadFailed
                store.update(updated)
            }
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

#Preview("Recording active") {
    RecordingTrayPreview()
}

/// Simulates the recording tray UI for Xcode canvas preview
private struct RecordingTrayPreview: View {
    @State private var level: Float = -30
    private let timer = Timer.publish(every: 0.05, on: .main, in: .common).autoconnect()

    var body: some View {
        NavigationStack {
            List {
                ForEach(0..<3) { i in
                    RecordingRow(recording: Recording(
                        id: UUID(),
                        capturedAt: Date().addingTimeInterval(Double(-i) * 3600),
                        duration: Double(60 + i * 45),
                        privacyMode: i == 1 ? .private : .standard,
                        status: .done,
                        title: ["Weekly standup", "Voice memo", "Design review"][i]
                    ))
                }
            }
            .navigationTitle("Sotto")
            .safeAreaInset(edge: .bottom) {
                VStack(spacing: 16) {
                    WaveformView(level: level)
                        .frame(height: 48)
                        .padding(.horizontal, 32)

                    Text("01:23.4")
                        .font(.system(size: 34, weight: .light, design: .monospaced))

                    Button {} label: {
                        ZStack {
                            Circle()
                                .fill(.red)
                                .frame(width: 72, height: 72)
                            RoundedRectangle(cornerRadius: 6)
                                .fill(.white)
                                .frame(width: 26, height: 26)
                        }
                    }
                    .padding(.bottom, 8)
                }
                .padding(.horizontal)
                .padding(.bottom, 24)
                .frame(maxWidth: .infinity)
                .background(alignment: .bottom) {
                    Color(.systemBackground)
                        .ignoresSafeArea(edges: .bottom)
                }
                .background(alignment: .top) {
                    LinearGradient(
                        colors: [
                            Color(.systemBackground).opacity(0),
                            Color(.systemBackground),
                        ],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                    .frame(height: 20)
                    .offset(y: -20)
                }
            }
        }
        .onReceive(timer) { _ in
            level = Float.random(in: -55 ... -5)
        }
    }
}
