import QtQuick
import EX30 1.0

// Clock + ambient temperature, top-right on every screen.
Column {
    id: corner
    spacing: 4

    property string now: Qt.formatTime(new Date(), "HH:mm")

    Timer {
        interval: 10000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: corner.now = Qt.formatTime(new Date(), "HH:mm")
    }

    Text {
        anchors.right: parent.right
        text: corner.now
        color: Theme.text
        font.family: Theme.sans
        font.pixelSize: 46
        font.weight: Font.Medium
        font.features: ({ "tnum": 1 })
        Behavior on color { ColorAnimation { duration: 400 } }
    }
    Text {
        anchors.right: parent.right
        text: Math.round(vehicle.ambientTemp) + "° outside"
        color: Theme.subtext
        font.family: Theme.sans
        font.pixelSize: 22
    }
}
