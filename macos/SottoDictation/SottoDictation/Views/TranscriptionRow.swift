import SwiftUI

struct TranscriptionRow: View {
    let transcription: Transcription
    @State private var copied = false

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            VStack(alignment: .leading, spacing: 4) {
                Text(transcription.text)
                    .font(.system(.body, design: .default))
                    .lineLimit(3)
                    .foregroundStyle(.primary)

                HStack(spacing: 8) {
                    Text(timeAgo)
                        .font(.caption)
                        .foregroundStyle(.secondary)

                    if transcription.durationSeconds > 0 {
                        Text(formattedDuration)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }

            Spacer()

            Button(action: copyText) {
                Image(systemName: copied ? "checkmark" : "doc.on.doc")
                    .font(.system(size: 12))
                    .foregroundStyle(copied ? .green : .secondary)
            }
            .buttonStyle(.borderless)
            .help("Copy to clipboard")
        }
        .padding(.vertical, 6)
        .padding(.horizontal, 10)
        .contentShape(Rectangle())
    }

    private func copyText() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(transcription.text, forType: .string)
        copied = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
            copied = false
        }
    }

    private var timeAgo: String {
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .abbreviated
        return formatter.localizedString(for: transcription.createdAt, relativeTo: Date())
    }

    private var formattedDuration: String {
        let seconds = Int(transcription.durationSeconds)
        if seconds < 60 {
            return "\(seconds)s"
        }
        return "\(seconds / 60)m \(seconds % 60)s"
    }
}
