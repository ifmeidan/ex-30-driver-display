import QtQuick
import QtQuick.Shapes
import EX30 1.0

// Turn indicator — notched arrow, hard on/off blink like a real relay.
Item {
    id: blinker
    property bool active: false
    property int dir: 1     // 1 = points right, -1 = points left

    width: 56
    height: 52
    visible: active
    opacity: 0

    Shape {
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        transform: Scale {
            xScale: blinker.dir
            origin.x: blinker.width / 2
        }

        ShapePath {
            strokeColor: "transparent"
            fillColor: Theme.amber
            joinStyle: ShapePath.RoundJoin
            startX: 6; startY: 0
            PathLine { x: 52; y: 26 }
            PathLine { x: 6; y: 52 }
            PathLine { x: 16; y: 26 }
            PathLine { x: 6; y: 0 }
        }
    }

    SequentialAnimation on opacity {
        running: blinker.active
        loops: Animation.Infinite
        NumberAnimation { to: 1; duration: 60 }
        PauseAnimation { duration: 360 }
        NumberAnimation { to: 0.08; duration: 90 }
        PauseAnimation { duration: 290 }
    }
    onActiveChanged: if (!active) opacity = 0
}
