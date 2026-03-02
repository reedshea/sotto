# Sotto iOS: Job Status Polling Implementation Plan

## Background

Sotto is a private voice transcription app. The iOS app records audio and uploads it to a self-hosted Python server (FastAPI). The server transcribes audio with Whisper, generates a title/summary with an LLM (Ollama or Anthropic), and stores the result.

**The problem:** The iOS app uploads audio and immediately forgets about it. It never captures the server's response (which contains the job UUID), never polls for status updates, and recordings are stuck showing "Uploading" forever. The server API is fully ready for polling — this is purely an iOS-side implementation.

## Server API (already implemented, no changes needed)

**`POST /upload`** — Upload audio file. Returns:
```json
{ "uuid": "abc-123", "status": "pending" }
```

**`GET /jobs/{uuid}`** — Poll job status. Returns:
```json
{
  "uuid": "abc-123",
  "status": "pending|transcribing|summarizing|completed|failed",
  "privacy": "private|standard",
  "created_at": "2026-03-02T10:00:00",
  "title": "Meeting notes about...",
  "summary": "Discussion of quarterly goals...",
  "transcript": "Full transcript text...",
  "duration_seconds": 45.2,
  "error_message": null
}
```

Auth: `Authorization: Bearer <token>` header on all requests.

## Current iOS Architecture

- **SwiftUI app** with `@EnvironmentObject` pattern
- **RecordingStore** (`@Published var recordings: [Recording]`) — persists to UserDefaults
- **DestinationStore** — holds server URL + auth token
- **UploadManager** — singleton, currently fire-and-forget via background URLSession

### Current file locations (all under `ios/Sotto/Sotto/`)
```
Models/Recording.swift        — Recording struct + RecordingStatus enum
Models/Destination.swift       — Server connection config (url, keySecret)
Services/UploadManager.swift   — Audio upload (currently broken — no response handling)
Services/RecordingStore.swift  — Local recording persistence (UserDefaults)
Services/DestinationStore.swift — Server destination persistence
Views/RecordingListView.swift  — Main list + record button + stopRecording()
Views/RecordingRow.swift       — Row display + StatusBadge component
Views/SettingsView.swift       — Settings UI
Views/WaveformView.swift       — Recording waveform animation
SottoApp.swift                 — App entry point, environment injection
```

## Changes Required

### 1. `Models/Recording.swift` — Add `.failed` status + server status mapping

**Current `RecordingStatus`:** `savedLocally`, `uploading`, `uploaded`, `transcribing`, `done`, `uploadFailed`

**Add:**
- New case `.failed` for server-side processing errors (distinct from `.uploadFailed` which means the upload itself didn't reach the server)
- Static method `fromServer(_ status: String) -> RecordingStatus` to map server statuses to iOS statuses

**Status mapping:**
| Server status | iOS status | Meaning |
|--------------|------------|---------|
| `pending` | `.uploaded` | File on server, waiting to process |
| `transcribing` | `.transcribing` | Whisper is running |
| `summarizing` | `.transcribing` | LLM generating title/summary (lump together) |
| `completed` | `.done` | All done |
| `failed` | `.failed` | Server-side error |

```swift
enum RecordingStatus: String, Codable {
    case savedLocally = "saved_locally"
    case uploading
    case uploaded
    case transcribing
    case done
    case failed
    case uploadFailed = "upload_failed"

    static func fromServer(_ status: String) -> RecordingStatus {
        switch status {
        case "pending":      return .uploaded
        case "transcribing": return .transcribing
        case "summarizing":  return .transcribing
        case "completed":    return .done
        case "failed":       return .failed
        default:             return .uploaded
        }
    }
}
```

### 2. `Services/UploadManager.swift` — Rewrite as async, capture response

Replace the background URLSession fire-and-forget with an async function that returns the server's `UploadResponse` (containing the UUID).

**Current:** Background URLSession, `delegate: nil`, discards response.
**New:** `URLSession.shared` with `async/await`, returns parsed response.

```swift
import Foundation

struct UploadResponse: Decodable {
    let uuid: String
    let status: String
}

class UploadManager {
    static let shared = UploadManager()

    func upload(recording: Recording, destination: Destination) async throws -> UploadResponse {
        guard let fileURL = recording.localFileURL else {
            throw URLError(.fileDoesNotExist)
        }

        var request = URLRequest(url: URL(string: "\(destination.url)/upload")!)
        request.httpMethod = "POST"
        request.setValue("Bearer \(destination.keySecret)", forHTTPHeaderField: "Authorization")

        let boundary = UUID().uuidString
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")

        // Build multipart body — same fields as before (privacy + file)
        var body = Data()

        // Privacy field
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"privacy\"\r\n\r\n".data(using: .utf8)!)
        body.append("\(recording.privacyMode.rawValue)\r\n".data(using: .utf8)!)

        // File field
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"file\"; filename=\"\(fileURL.lastPathComponent)\"\r\n".data(using: .utf8)!)
        body.append("Content-Type: audio/m4a\r\n\r\n".data(using: .utf8)!)
        if let audioData = try? Data(contentsOf: fileURL) {
            body.append(audioData)
        }
        body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)

        let (data, response) = try await URLSession.shared.upload(for: request, from: body)

        guard let http = response as? HTTPURLResponse, http.statusCode == 201 else {
            throw URLError(.badServerResponse)
        }

        return try JSONDecoder().decode(UploadResponse.self, from: data)
    }
}
```

### 3. `Views/RecordingListView.swift` — Async upload with UUID capture

Update `stopRecording()` to use async/await. On success, set `serverUUID` and transition to `.uploaded`. On failure, transition to `.uploadFailed`.

```swift
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
        } catch {
            var updated = recording
            updated.status = .uploadFailed
            store.update(updated)
        }
    }
}
```

### 4. **New file:** `Services/JobPoller.swift` — Polling service

Create a new `ObservableObject` that manages a timer-based polling loop for active jobs.

**Polling rules:**
- Poll every **3 seconds** while there are active (non-terminal) jobs
- Only poll recordings where `serverUUID != nil` and status is `.uploaded` or `.transcribing`
- Stop the timer automatically when all jobs are terminal
- On each poll response, map server status → iOS status and update title/summary/error_message
- Re-start polling when `startIfNeeded()` is called (e.g., after a new upload completes)

**Server response model for polling:**
```swift
struct JobStatusResponse: Decodable {
    let uuid: String
    let status: String
    let title: String?
    let summary: String?
    let transcript: String?
    let durationSeconds: Double?
    let errorMessage: String?

    enum CodingKeys: String, CodingKey {
        case uuid, status, title, summary, transcript
        case durationSeconds = "duration_seconds"
        case errorMessage = "error_message"
    }
}
```

**JobPoller class outline:**
```swift
class JobPoller: ObservableObject {
    private var timer: Timer?
    private let interval: TimeInterval = 3.0
    private weak var store: RecordingStore?
    private weak var destinationStore: DestinationStore?

    init(store: RecordingStore, destinationStore: DestinationStore) {
        self.store = store
        self.destinationStore = destinationStore
    }

    func startIfNeeded() {
        guard timer == nil else { return }
        guard hasActiveJobs else { return }
        timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            self?.pollActiveJobs()
        }
    }

    func stop() {
        timer?.invalidate()
        timer = nil
    }

    private var hasActiveJobs: Bool {
        store?.recordings.contains { $0.serverUUID != nil && ($0.status == .uploaded || $0.status == .transcribing) } ?? false
    }

    private func pollActiveJobs() {
        guard let store, let destinationStore, let destination = destinationStore.destination else { return }

        let active = store.recordings.filter { r in
            r.serverUUID != nil && (r.status == .uploaded || r.status == .transcribing)
        }

        if active.isEmpty {
            stop()
            return
        }

        for recording in active {
            Task { await pollJob(recording, destination: destination) }
        }
    }

    @MainActor
    private func pollJob(_ recording: Recording, destination: Destination) async {
        guard let serverUUID = recording.serverUUID else { return }

        var request = URLRequest(url: URL(string: "\(destination.url)/jobs/\(serverUUID)")!)
        request.setValue("Bearer \(destination.keySecret)", forHTTPHeaderField: "Authorization")

        guard let (data, _) = try? await URLSession.shared.data(for: request),
              let response = try? JSONDecoder().decode(JobStatusResponse.self, from: data)
        else { return }  // Silently skip on network error, will retry next poll

        let newStatus = RecordingStatus.fromServer(response.status)
        guard newStatus != recording.status || response.title != recording.title else { return }

        var updated = recording
        updated.status = newStatus
        if let title = response.title { updated.title = title }
        if let summary = response.summary { updated.summary = summary }
        store?.update(updated)

        // Stop polling if no more active jobs
        if !hasActiveJobs { stop() }
    }
}
```

### 5. `SottoApp.swift` — Wire up JobPoller

Inject the poller into the app and start it on appear.

```swift
@main
struct SottoApp: App {
    @StateObject private var recordingStore: RecordingStore
    @StateObject private var destinationStore: DestinationStore
    @StateObject private var poller: JobPoller

    init() {
        let store = RecordingStore()
        let destStore = DestinationStore()
        _recordingStore = StateObject(wrappedValue: store)
        _destinationStore = StateObject(wrappedValue: destStore)
        _poller = StateObject(wrappedValue: JobPoller(store: store, destinationStore: destStore))
    }

    var body: some Scene {
        WindowGroup {
            RecordingListView()
                .environmentObject(recordingStore)
                .environmentObject(destinationStore)
                .onAppear { poller.startIfNeeded() }
        }
    }
}
```

### 6. `Views/RecordingRow.swift` — Add `.failed` to StatusBadge

Add the new case to both computed properties in `StatusBadge`:

```swift
private var label: String {
    switch status {
    case .savedLocally: "Saved"
    case .uploading: "Uploading"
    case .uploaded: "Uploaded"
    case .transcribing: "Transcribing"
    case .done: "Done"
    case .failed: "Failed"
    case .uploadFailed: "Retry"
    }
}

private var color: Color {
    switch status {
    case .savedLocally: .gray
    case .uploading: .blue
    case .uploaded: .blue
    case .transcribing: .orange
    case .done: .green
    case .failed: .red
    case .uploadFailed: .red
    }
}
```

## Files summary

| File | Action |
|------|--------|
| `ios/Sotto/Sotto/Models/Recording.swift` | Add `.failed` status + `fromServer()` mapping |
| `ios/Sotto/Sotto/Services/UploadManager.swift` | Rewrite as async, return `UploadResponse` |
| `ios/Sotto/Sotto/Services/JobPoller.swift` | **New file** — polling service |
| `ios/Sotto/Sotto/Views/RecordingListView.swift` | Async `stopRecording()` with UUID capture |
| `ios/Sotto/Sotto/Views/RecordingRow.swift` | Add `.failed` case to StatusBadge |
| `ios/Sotto/Sotto/SottoApp.swift` | Wire up JobPoller, change init pattern |

## Verification
1. Build in Xcode — no compile errors
2. Run `sotto start` on the server with Ollama running
3. Record audio → status should transition: Uploading → Uploaded → Transcribing → Done
4. Title and summary should populate in the row once processing completes
5. Stop Ollama → record audio → should still show Done (server saves transcript with fallback title)
6. Stop the sotto server entirely → record audio → should show Retry (upload failed)
