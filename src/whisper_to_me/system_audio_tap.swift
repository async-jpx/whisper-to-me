// system_audio_tap: stream macOS system audio to stdout as raw PCM.
//
// Captures the pre-mixer audio of every app (Zoom, Teams, browser tabs…)
// via ScreenCaptureKit — audio only, no video frames are delivered.
// Output format: 16 kHz mono Float32, little-endian, raw frames on stdout.
// Requires the "Screen & System Audio Recording" permission (macOS prompts
// on first run). Runs until stdin closes or the process is terminated.

import AVFoundation
import Foundation
import ScreenCaptureKit

let CAPTURE_RATE = 48_000
let TARGET_RATE = 16_000
let DECIMATE = CAPTURE_RATE / TARGET_RATE  // 3:1

final class AudioSink: NSObject, SCStreamOutput, SCStreamDelegate {
    let out = FileHandle.standardOutput
    var carry: [Float32] = []  // frames left over when a buffer isn't divisible by 3

    func stream(
        _ stream: SCStream,
        didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
        of type: SCStreamOutputType
    ) {
        guard type == .audio, sampleBuffer.isValid else { return }

        _ = try? sampleBuffer.withAudioBufferList { audioBufferList, _ in
            let buffers = audioBufferList  // UnsafeMutableAudioBufferListPointer
            guard let first = buffers.first, first.mData != nil else { return }
            let firstChannels = Int(max(first.mNumberChannels, 1))
            let frames = Int(first.mDataByteSize) / MemoryLayout<Float32>.size / firstChannels
            guard frames > 0 else { return }

            // Downmix to mono across all buffers/channels (handles both
            // interleaved single-buffer and planar per-channel layouts).
            var mono = [Float32](repeating: 0, count: frames)
            var sources = 0
            for buffer in buffers {
                guard let data = buffer.mData else { continue }
                let ptr = data.assumingMemoryBound(to: Float32.self)
                let channels = Int(max(buffer.mNumberChannels, 1))
                if channels == 1 {
                    for f in 0..<frames { mono[f] += ptr[f] }
                } else {
                    for f in 0..<frames {
                        var sum: Float32 = 0
                        for c in 0..<channels { sum += ptr[f * channels + c] }
                        mono[f] += sum / Float32(channels)
                    }
                }
                sources += 1
            }
            guard sources > 0 else { return }
            if sources > 1 {
                for f in 0..<frames { mono[f] /= Float32(sources) }
            }

            // Decimate 48k -> 16k by averaging triples, carrying remainders.
            carry.append(contentsOf: mono)
            let outFrames = carry.count / DECIMATE
            guard outFrames > 0 else { return }
            var down = [Float32](repeating: 0, count: outFrames)
            for i in 0..<outFrames {
                var sum: Float32 = 0
                for j in 0..<DECIMATE { sum += carry[i * DECIMATE + j] }
                down[i] = sum / Float32(DECIMATE)
            }
            carry.removeFirst(outFrames * DECIMATE)
            down.withUnsafeBufferPointer { buf in
                out.write(Data(buffer: buf))
            }
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        FileHandle.standardError.write(Data("stream stopped: \(error)\n".utf8))
        exit(1)
    }
}

let sink = AudioSink()
// Strong reference so the stream outlives the setup Task — without this the
// SCStream deallocates as soon as the Task returns and capture silently stops.
var activeStream: SCStream?

func log(_ message: String) {
    FileHandle.standardError.write(Data((message + "\n").utf8))
}

Task {
    do {
        if !CGPreflightScreenCaptureAccess() {
            log("no screen/audio capture permission — requesting (approve the macOS dialog)…")
            if !CGRequestScreenCaptureAccess() {
                log("permission denied. Enable your terminal app under System Settings → Privacy & Security → Screen & System Audio Recording, then retry.")
                exit(2)
            }
        }
        log("requesting shareable content (may trigger permission prompt)…")
        let content = try await SCShareableContent.excludingDesktopWindows(
            false, onScreenWindowsOnly: false
        )
        log("got shareable content")
        guard let display = content.displays.first else {
            FileHandle.standardError.write(Data("no display found\n".utf8))
            exit(1)
        }
        let filter = SCContentFilter(display: display, excludingWindows: [])

        let config = SCStreamConfiguration()
        config.capturesAudio = true
        config.excludesCurrentProcessAudio = true
        config.sampleRate = CAPTURE_RATE
        config.channelCount = 2
        // A realistic (if small) video config is required: with degenerate
        // dimensions/frame rates SCStream silently delivers no buffers at all,
        // audio included. Video frames are received and discarded.
        config.width = 640
        config.height = 360
        config.minimumFrameInterval = CMTime(value: 1, timescale: 10)

        let stream = SCStream(filter: filter, configuration: config, delegate: sink)
        activeStream = stream
        try stream.addStreamOutput(
            sink, type: .audio, sampleHandlerQueue: DispatchQueue(label: "audio")
        )
        try stream.addStreamOutput(
            sink, type: .screen, sampleHandlerQueue: DispatchQueue(label: "video")
        )
        try await stream.startCapture()
        log("capturing")
    } catch {
        FileHandle.standardError.write(Data("failed to start: \(error)\n".utf8))
        exit(1)
    }
}

// Exit when the parent closes our stdin (or kills us).
FileHandle.standardInput.readabilityHandler = { handle in
    if handle.availableData.isEmpty {
        exit(0)
    }
}
RunLoop.main.run()
