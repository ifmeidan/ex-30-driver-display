import QtQuick
import EX30 1.0

// Drive layout across 1920x480:
// [battery] [brake] [blinker  SPEED  blinker] [power] [trip]
Item {
    id: screen
    property bool awake: false

    // Gentle rise on wake
    transform: Translate {
        y: screen.awake ? 0 : 12
        Behavior on y { NumberAnimation { duration: 600; easing.type: Easing.OutCubic } }
    }

    SocCluster {
        x: 72
        y: 64
    }

    PedalBar {
        x: 556
        y: 0
        value: vehicle.brakePct
        fillColor: Theme.red
        label: "BRAKE"
    }

    BlinkerArrow {
        x: 682
        y: 78
        dir: -1
        active: vehicle.blinkerLeft
    }
    BlinkerArrow {
        x: 1182
        y: 78
        dir: 1
        active: vehicle.blinkerRight
    }

    SpeedCluster {
        anchors.horizontalCenter: parent.horizontalCenter
        y: 0
    }

    PowerBar {
        x: 1264
        y: 0
    }

    TripPanel {
        x: 1518
        y: 118
    }
}
