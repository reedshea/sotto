import SwiftUI

@main
struct SottoDictationApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate

    var body: some Scene {
        // Settings window opened via menu bar
        Settings {
            SettingsView()
                .environmentObject(appDelegate.store)
        }
    }
}

@MainActor
class AppDelegate: NSObject, NSApplicationDelegate, ObservableObject {
    let store = TranscriptionStore()
    let audioRecorder = AudioRecorder()
    let transcriptionService = TranscriptionService()

    private var statusItem: NSStatusItem!
    private var popover: NSPopover!
    private var hotkeyManager: HotkeyManager?
    private var recordingPanel: NSPanel?

    func applicationDidFinishLaunching(_ notification: Notification) {
        setupStatusItem()
        setupPopover()
        setupHotkey()

        // Hide dock icon — this is a menu bar app
        NSApp.setActivationPolicy(.accessory)
    }

    // MARK: - Status Bar

    private func setupStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)

        if let button = statusItem.button {
            button.image = NSImage(systemSymbolName: "waveform", accessibilityDescription: "Sotto Dictation")
            button.action = #selector(togglePopover)
            button.target = self
        }
    }

    private func setupPopover() {
        popover = NSPopover()
        popover.contentSize = NSSize(width: 360, height: 420)
        popover.behavior = .transient
        popover.contentViewController = NSHostingController(
            rootView: PopoverContentView(
                delegate: self
            )
            .environmentObject(store)
            .environmentObject(audioRecorder)
        )
    }

    @objc private func togglePopover() {
        guard let button = statusItem.button else { return }
        if popover.isShown {
            popover.performClose(nil)
        } else {
            popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
            NSApp.activate(ignoringOtherApps: true)
        }
    }

    // MARK: - Hotkey

    private func setupHotkey() {
        hotkeyManager = HotkeyManager { [weak self] in
            self?.toggleRecording()
        }
        hotkeyManager?.register()
    }

    // MARK: - Recording Flow

    func toggleRecording() {
        if audioRecorder.isRecording {
            stopAndTranscribe()
        } else {
            startRecording()
        }
    }

    private func startRecording() {
        do {
            _ = try audioRecorder.startRecording()
            updateStatusIcon(recording: true)
            showRecordingIndicator()
        } catch {
            showNotification(title: "Recording Failed", body: error.localizedDescription)
        }
    }

    private func stopAndTranscribe() {
        guard let fileURL = audioRecorder.stopRecording() else { return }
        updateStatusIcon(recording: false)
        hideRecordingIndicator()
        updateStatusIcon(transcribing: true)

        Task {
            defer {
                audioRecorder.cleanup(url: fileURL)
                updateStatusIcon(transcribing: false)
            }

            do {
                let response = try await transcriptionService.transcribe(
                    fileURL: fileURL,
                    config: store.serverConfig
                )

                guard !response.text.isEmpty else { return }

                let transcription = Transcription(
                    text: response.text,
                    durationSeconds: response.duration_seconds
                )
                store.add(transcription)

                // Paste into active app
                PasteService.pasteText(response.text)
            } catch {
                showNotification(title: "Transcription Failed", body: error.localizedDescription)
            }
        }
    }

    // MARK: - Status Icon Updates

    private func updateStatusIcon(recording: Bool) {
        statusItem.button?.image = NSImage(
            systemSymbolName: recording ? "waveform.circle.fill" : "waveform",
            accessibilityDescription: "Sotto Dictation"
        )
    }

    private func updateStatusIcon(transcribing: Bool) {
        statusItem.button?.image = NSImage(
            systemSymbolName: transcribing ? "ellipsis.circle" : "waveform",
            accessibilityDescription: "Sotto Dictation"
        )
    }

    // MARK: - Recording Indicator Panel

    private func showRecordingIndicator() {
        guard let screen = NSScreen.main else { return }

        let panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 160, height: 32),
            styleMask: [.nonactivatingPanel, .hudWindow],
            backing: .buffered,
            defer: false
        )
        panel.isFloatingPanel = true
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true

        let hostingView = NSHostingView(rootView: RecordingIndicator(audioLevel: audioRecorder.audioLevel))
        panel.contentView = hostingView

        // Position near top-center of screen
        let screenFrame = screen.visibleFrame
        let x = screenFrame.midX - 80
        let y = screenFrame.maxY - 50
        panel.setFrameOrigin(NSPoint(x: x, y: y))
        panel.orderFront(nil)

        recordingPanel = panel
    }

    private func hideRecordingIndicator() {
        recordingPanel?.orderOut(nil)
        recordingPanel = nil
    }

    // MARK: - Notifications

    private func showNotification(title: String, body: String) {
        let notification = NSUserNotification()
        notification.title = title
        notification.informativeText = body
        NSUserNotificationCenter.default.deliver(notification)
    }
}

// MARK: - Popover Content

struct PopoverContentView: View {
    let delegate: AppDelegate
    @EnvironmentObject var store: TranscriptionStore
    @EnvironmentObject var audioRecorder: AudioRecorder

    var body: some View {
        VStack(spacing: 0) {
            // Record button bar
            HStack {
                Button(action: { delegate.toggleRecording() }) {
                    HStack(spacing: 6) {
                        Image(systemName: audioRecorder.isRecording ? "stop.circle.fill" : "mic.circle.fill")
                            .font(.system(size: 18))
                            .foregroundStyle(audioRecorder.isRecording ? .red : .accentColor)
                        Text(audioRecorder.isRecording ? "Stop" : "Dictate")
                            .font(.system(size: 13, weight: .medium))
                    }
                }
                .buttonStyle(.borderless)

                Spacer()

                Button(action: {
                    NSApp.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil)
                }) {
                    Image(systemName: "gear")
                        .font(.system(size: 14))
                        .foregroundStyle(.secondary)
                }
                .buttonStyle(.borderless)
                .help("Settings")
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)

            if audioRecorder.isRecording {
                RecordingIndicator(audioLevel: audioRecorder.audioLevel)
                    .padding(.horizontal, 12)
                    .padding(.bottom, 8)
            }

            Divider()

            TranscriptionListView()
                .environmentObject(store)
        }
    }
}
