import QtQuick
import EX30 1.0

// Always-visible data-link indicator: green dot = connected,
// pulsing amber = connecting / lost.
Row {
    id: badge
    property string label: ""
    property bool ok: false

    spacing: 8
    opacity: ok ? 0.65 : 1
    Behavior on opacity { NumberAnimation { duration: 300 } }

    Rectangle {
        id: dot
        width: 8
        height: 8
        radius: 4
        anchors.verticalCenter: parent.verticalCenter
        color: badge.ok ? Theme.green : Theme.amber
        Behavior on color { ColorAnimation { duration: 300 } }

        SequentialAnimation on opacity {
            running: !badge.ok
            loops: Animation.Infinite
            onRunningChanged: if (!running) dot.opacity = 1
            NumberAnimation { from: 1; to: 0.25; duration: 750; easing.type: Easing.InOutSine }
            NumberAnimation { from: 0.25; to: 1; duration: 750; easing.type: Easing.InOutSine }
        }
    }
    Text {
        anchors.verticalCenter: parent.verticalCenter
        text: badge.label
        color: Theme.subtext
        font.family: Theme.sans
        font.pixelSize: 14
        font.weight: Font.DemiBold
        font.letterSpacing: 2
    }
}
