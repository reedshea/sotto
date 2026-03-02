import SwiftUI

struct TranscriptionListView: View {
    @EnvironmentObject var store: TranscriptionStore

    var body: some View {
        VStack(spacing: 0) {
            header

            Divider()

            if store.transcriptions.isEmpty {
                emptyState
            } else {
                transcriptionList
            }
        }
        .frame(width: 360, height: 420)
    }

    private var header: some View {
        HStack {
            Text("Recent Transcriptions")
                .font(.headline)

            Spacer()

            if !store.transcriptions.isEmpty {
                Button("Clear") {
                    store.clear()
                }
                .buttonStyle(.borderless)
                .font(.caption)
                .foregroundStyle(.secondary)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
    }

    private var emptyState: some View {
        VStack(spacing: 8) {
            Spacer()
            Image(systemName: "waveform")
                .font(.system(size: 32))
                .foregroundStyle(.tertiary)
            Text("No transcriptions yet")
                .font(.subheadline)
                .foregroundStyle(.secondary)
            Text("Press \u{2303}\u{2325}S to start dictating")
                .font(.caption)
                .foregroundStyle(.tertiary)
            Spacer()
        }
        .frame(maxWidth: .infinity)
    }

    private var transcriptionList: some View {
        ScrollView {
            LazyVStack(spacing: 0) {
                ForEach(store.transcriptions) { transcription in
                    TranscriptionRow(transcription: transcription)
                    Divider()
                        .padding(.leading, 10)
                }
            }
        }
    }
}
