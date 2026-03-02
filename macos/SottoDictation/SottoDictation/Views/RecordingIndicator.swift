import SwiftUI

/// A small floating overlay shown near the menu bar while recording.
struct RecordingIndicator: View {
    let audioLevel: Float
    @State private var pulse = false

    var body: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(.red)
                .frame(width: 8, height: 8)
                .scaleEffect(pulse ? 1.3 : 1.0)
                .animation(.easeInOut(duration: 0.6).repeatForever(autoreverses: true), value: pulse)
                .onAppear { pulse = true }

            // Simple level meter
            HStack(spacing: 2) {
                ForEach(0..<5, id: \.self) { i in
                    RoundedRectangle(cornerRadius: 1)
                        .fill(barColor(index: i))
                        .frame(width: 3, height: barHeight(index: i))
                }
            }
            .frame(height: 16)

            Text("Listening...")
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(.primary)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 8))
    }

    private func barHeight(index: Int) -> CGFloat {
        let threshold = Float(index) / 5.0
        let active = audioLevel > threshold
        return active ? CGFloat(8 + (audioLevel - threshold) * 16) : 4
    }

    private func barColor(index: Int) -> Color {
        let threshold = Float(index) / 5.0
        return audioLevel > threshold ? .red : .red.opacity(0.3)
    }
}
