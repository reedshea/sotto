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
