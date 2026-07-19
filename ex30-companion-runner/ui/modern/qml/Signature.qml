import QtQuick
import EX30 1.0

// "ifmeidan OS" — quiet credit line on the boot and loading screens.
Row {
    id: sig
    spacing: 8
    opacity: 0.42

    Text {
        id: handle
        text: "ifmeidan"
        color: Theme.bootText
        font.family: Theme.sans
        font.pixelSize: 16
        font.weight: Font.DemiBold
        font.letterSpacing: 3
    }
    Text {
        anchors.baseline: handle.baseline
        text: "OS"
        color: Theme.bootText
        font.family: Theme.sans
        font.pixelSize: 15
        font.weight: Font.Light
        font.letterSpacing: 1.2
    }
}
