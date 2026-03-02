import Carbon
import Cocoa
import Combine

/// Manages a global keyboard shortcut for toggling dictation.
/// Default hotkey: Control+Option+S
class HotkeyManager {
    private var eventHandler: EventHandlerRef?
    private let onToggle: () -> Void

    init(onToggle: @escaping () -> Void) {
        self.onToggle = onToggle
    }

    func register() {
        var hotKeyID = EventHotKeyID()
        hotKeyID.signature = OSType(0x534F5454) // "SOTT"
        hotKeyID.id = 1

        var hotKeyRef: EventHotKeyRef?

        // Control+Option+S
        let modifiers: UInt32 = UInt32(controlKey | optionKey)
        let keyCode: UInt32 = 1 // 's' key

        RegisterEventHotKey(keyCode, modifiers, hotKeyID, GetApplicationEventTarget(), 0, &hotKeyRef)

        var eventType = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))

        let handler: EventHandlerUPP = { _, event, userData -> OSStatus in
            guard let userData = userData else { return OSStatus(eventNotHandledErr) }
            let manager = Unmanaged<HotkeyManager>.fromOpaque(userData).takeUnretainedValue()
            Task { @MainActor in
                manager.onToggle()
            }
            return noErr
        }

        let selfPtr = Unmanaged.passUnretained(self).toOpaque()
        InstallEventHandler(GetApplicationEventTarget(), handler, 1, &eventType, selfPtr, &eventHandler)
    }

    deinit {
        if let handler = eventHandler {
            RemoveEventHandler(handler)
        }
    }
}
