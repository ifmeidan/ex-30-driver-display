import QtQuick
import EX30 1.0

// BLIS warning — soft amber light bleeding in from the screen edge,
// breathing while active. Mirrors the car's mirror-mounted lamp.
Item {
    id: glow
    property int side: -1      // -1 = left edge, 1 = right edge
    property bool active: false

    width: 34
    anchors.top: parent.top
    anchors.bottom: parent.bottom
    x: side < 0 ? 0 : parent.width - width

    opacity: active ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: 160 } }

    Rectangle {
        id: strip
        anchors.fill: parent
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop {
                position: 0
                color: glow.side < 0 ? Qt.alpha(Theme.amber, 0.95) : "transparent"
            }
            GradientStop {
                position: 1
                color: glow.side < 0 ? "transparent" : Qt.alpha(Theme.amber, 0.95)
            }
        }

        SequentialAnimation on opacity {
            running: glow.active
            loops: Animation.Infinite
            NumberAnimation { from: 1; to: 0.55; duration: 650; easing.type: Easing.InOutSine }
            NumberAnimation { from: 0.55; to: 1; duration: 650; easing.type: Easing.InOutSine }
        }
    }
}
