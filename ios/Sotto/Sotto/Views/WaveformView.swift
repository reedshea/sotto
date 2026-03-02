import Combine
import SwiftUI

struct WaveformView: View {
    let level: Float
    @State private var bars: [CGFloat] = Array(repeating: 0.1, count: 30)

    var body: some View {
        HStack(spacing: 2) {
            ForEach(0..<bars.count, id: \.self) { index in
                RoundedRectangle(cornerRadius: 1.5)
                    .fill(.primary)
                    .frame(width: 3, height: max(3, bars[index] * 40))
            }
        }
        .onChange(of: level) { _, newLevel in
            // Shift bars left, add new level on right
            bars.removeFirst()
            let normalized = CGFloat(max(0, (newLevel + 60) / 60))
            bars.append(normalized)
        }
    }
}

// MARK: - Previews

#Preview("Waveform — silent") {
    WaveformView(level: -160)
        .frame(height: 40)
        .padding()
}

#Preview("Waveform — mid level") {
    WaveformView(level: -30)
        .frame(height: 40)
        .padding()
}

#Preview("Waveform — animated") {
    WaveformAnimatedPreview()
        .padding()
}

/// Helper that simulates changing audio levels for preview
private struct WaveformAnimatedPreview: View {
    @State private var level: Float = -60
    private let timer = Timer.publish(every: 0.05, on: .main, in: .common).autoconnect()

    var body: some View {
        WaveformView(level: level)
            .frame(height: 40)
            .onReceive(timer) { _ in
                // Simulate varying audio levels between -60 and -5
                level = Float.random(in: -55 ... -5)
            }
    }
}
