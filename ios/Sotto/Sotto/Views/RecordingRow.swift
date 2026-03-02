import SwiftUI

struct RecordingRow: View {
    let recording: Recording

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                if recording.privacyMode == .private {
                    Image(systemName: "lock.fill")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Text(recording.displayTitle)
                    .font(.headline)
                    .lineLimit(1)

                Spacer()

                StatusBadge(status: recording.status)
            }

            if let summary = recording.summary {
                Text(summary)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }

            HStack {
                Text(recording.capturedAt, style: .date)
                Text("·")
                Text(recording.formattedDuration)
            }
            .font(.caption)
            .foregroundStyle(.tertiary)
        }
        .padding(.vertical, 4)
    }
}

// MARK: - Previews

#Preview("Done — with summary") {
    RecordingRow(recording: Recording(
        id: UUID(),
        capturedAt: Date(),
        duration: 185,
        privacyMode: .standard,
        status: .done,
        title: "Weekly standup notes",
        summary: "Discussed sprint progress and blockers. Team agreed to push the release to next Friday."
    ))
    .padding()
}

#Preview("Uploading — private") {
    RecordingRow(recording: Recording(
        id: UUID(),
        capturedAt: Date().addingTimeInterval(-3600),
        duration: 42,
        privacyMode: .private,
        status: .uploading
    ))
    .padding()
}

#Preview("Transcribing") {
    RecordingRow(recording: Recording(
        id: UUID(),
        capturedAt: Date().addingTimeInterval(-7200),
        duration: 310,
        privacyMode: .standard,
        status: .transcribing,
        title: "Product brainstorm"
    ))
    .padding()
}

#Preview("Upload failed") {
    RecordingRow(recording: Recording(
        id: UUID(),
        capturedAt: Date().addingTimeInterval(-86400),
        duration: 67,
        privacyMode: .standard,
        status: .uploadFailed
    ))
    .padding()
}

#Preview("Saved locally — no title") {
    RecordingRow(recording: Recording(
        id: UUID(),
        capturedAt: Date(),
        duration: 12,
        privacyMode: .standard,
        status: .savedLocally
    ))
    .padding()
}

#Preview("All status badges") {
    VStack(spacing: 12) {
        StatusBadge(status: .savedLocally)
        StatusBadge(status: .uploading)
        StatusBadge(status: .uploaded)
        StatusBadge(status: .transcribing)
        StatusBadge(status: .done)
        StatusBadge(status: .uploadFailed)
    }
    .padding()
}

// MARK: - StatusBadge

struct StatusBadge: View {
    let status: RecordingStatus

    var body: some View {
        Text(label)
            .font(.caption2)
            .fontWeight(.medium)
            .padding(.horizontal, 8)
            .padding(.vertical, 2)
            .background(color.opacity(0.15))
            .foregroundStyle(color)
            .clipShape(Capsule())
    }

    private var label: String {
        switch status {
        case .savedLocally: "Saved"
        case .uploading: "Uploading"
        case .uploaded: "Uploaded"
        case .transcribing: "Transcribing"
        case .done: "Done"
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
        case .uploadFailed: .red
        }
    }
}
