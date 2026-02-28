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
