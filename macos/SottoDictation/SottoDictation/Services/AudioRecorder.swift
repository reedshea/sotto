import AVFoundation
import Foundation

@MainActor
class AudioRecorder: ObservableObject {
    @Published private(set) var isRecording = false
    @Published private(set) var audioLevel: Float = 0

    private var audioEngine: AVAudioEngine?
    private var audioFile: AVAudioFile?
    private var recordingURL: URL?
    private var levelTimer: Timer?

    /// Start recording audio from the default input device.
    /// Returns the file URL where audio is being written.
    func startRecording() throws -> URL {
        let engine = AVAudioEngine()
        let inputNode = engine.inputNode
        let format = inputNode.outputFormat(forBus: 0)

        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString)
            .appendingPathExtension("m4a")

        // Use AAC encoding for smaller file size
        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
            AVSampleRateKey: format.sampleRate,
            AVNumberOfChannelsKey: format.channelCount,
            AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue,
        ]
        let file = try AVAudioFile(forWriting: url, settings: settings)

        inputNode.installTap(onBus: 0, bufferSize: 4096, format: format) { [weak self] buffer, _ in
            try? file.write(from: buffer)

            // Calculate RMS level for visualization
            guard let channelData = buffer.floatChannelData?[0] else { return }
            let frames = Int(buffer.frameLength)
            var sum: Float = 0
            for i in 0..<frames {
                sum += channelData[i] * channelData[i]
            }
            let rms = sqrt(sum / Float(max(frames, 1)))
            let db = 20 * log10(max(rms, 0.0001))
            let normalized = max(0, min(1, (db + 50) / 50))

            Task { @MainActor [weak self] in
                self?.audioLevel = normalized
            }
        }

        try engine.start()

        self.audioEngine = engine
        self.audioFile = file
        self.recordingURL = url
        self.isRecording = true

        return url
    }

    /// Stop recording and return the URL of the recorded file.
    func stopRecording() -> URL? {
        audioEngine?.inputNode.removeTap(onBus: 0)
        audioEngine?.stop()
        audioEngine = nil
        audioFile = nil
        isRecording = false
        audioLevel = 0

        let url = recordingURL
        recordingURL = nil
        return url
    }

    /// Clean up a temporary recording file.
    func cleanup(url: URL) {
        try? FileManager.default.removeItem(at: url)
    }
}
