#include <QAccelerometer>
#include <QAccelerometerReading>
#include <QByteArray>
#include <QCommandLineOption>
#include <QCommandLineParser>
#include <QCompass>
#include <QCompassReading>
#include <QCoreApplication>
#include <QDateTime>
#include <QDebug>
#include <QGeoCoordinate>
#include <QGeoPositionInfo>
#include <QGeoPositionInfoSource>
#include <QGyroscope>
#include <QGyroscopeReading>
#include <QHash>
#include <QHostAddress>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonParseError>
#include <QLightSensor>
#include <QLightReading>
#include <QList>
#include <QMagnetometer>
#include <QMagnetometerReading>
#include <QOrientationReading>
#include <QOrientationSensor>
#include <QPointer>
#include <QProximityReading>
#include <QProximitySensor>
#include <QPressureReading>
#include <QPressureSensor>
#include <QRotationReading>
#include <QRotationSensor>
#include <QSensor>
#include <QSensorReading>
#include <QSet>
#include <QTcpServer>
#include <QTcpSocket>
#include <QTextStream>
#include <QFile>

#include <cmath>

namespace {

const int kProtocolVersion = 1;
const int kMaximumLineBytes = 64 * 1024;

QByteArray encode(const QJsonObject &object)
{
    return QJsonDocument(object).toJson(QJsonDocument::Compact) + '\n';
}

QJsonObject errorMessage(const QString &message)
{
    QJsonObject error;
    error.insert("type", "error");
    error.insert("message", message);
    return error;
}

} // namespace

class SensorBridge : public QTcpServer
{
    Q_OBJECT

public:
    SensorBridge(const QString &token, int dataRate, QObject *parent = 0)
        : QTcpServer(parent), m_token(token), m_dataRate(dataRate)
    {
        addSensor("accelerometer", new QAccelerometer(this));
        addSensor("gyroscope", new QGyroscope(this));
        addSensor("magnetometer", new QMagnetometer(this));
        addSensor("compass", new QCompass(this));
        addSensor("orientation", new QOrientationSensor(this));
        addSensor("light", new QLightSensor(this));
        addSensor("proximity", new QProximitySensor(this));
        addSensor("pressure", new QPressureSensor(this));
        addSensor("rotation", new QRotationSensor(this));
    }

protected:
    void incomingConnection(qintptr socketDescriptor)
    {
        QTcpSocket *socket = new QTcpSocket(this);
        if (!socket->setSocketDescriptor(socketDescriptor)) {
            qWarning() << "Could not accept connection:" << socket->errorString();
            socket->deleteLater();
            return;
        }

        m_clients.insert(socket, ClientState());
        connect(socket, SIGNAL(readyRead()), this, SLOT(readClient()));
        connect(socket, SIGNAL(disconnected()), this, SLOT(removeClient()));
    }

private slots:
    void readClient()
    {
        QTcpSocket *socket = qobject_cast<QTcpSocket *>(sender());
        if (!socket || !m_clients.contains(socket)) {
            return;
        }

        ClientState &client = m_clients[socket];
        client.buffer.append(socket->readAll());
        if (client.buffer.size() > kMaximumLineBytes) {
            fail(socket, "message exceeds 64 KiB");
            return;
        }

        int newline = -1;
        while ((newline = client.buffer.indexOf('\n')) >= 0) {
            const QByteArray line = client.buffer.left(newline);
            client.buffer.remove(0, newline + 1);
            if (line.isEmpty()) {
                continue;
            }
            handleMessage(socket, line);
            if (!m_clients.contains(socket)) {
                return;
            }
        }
    }

    void removeClient()
    {
        QTcpSocket *socket = qobject_cast<QTcpSocket *>(sender());
        if (!socket) {
            return;
        }
        m_clients.remove(socket);
        socket->deleteLater();
        reconcileSensors();
    }

    void publishReading()
    {
        QSensor *sensor = qobject_cast<QSensor *>(sender());
        if (!sensor || !m_sensorNames.contains(sensor)) {
            return;
        }

        const QString sensorName = m_sensorNames.value(sensor);
        QJsonObject values;

        if (sensorName == "accelerometer") {
            QAccelerometerReading *reading = static_cast<QAccelerometer *>(sensor)->reading();
            values.insert("x", reading->x());
            values.insert("y", reading->y());
            values.insert("z", reading->z());
        } else if (sensorName == "gyroscope") {
            QGyroscopeReading *reading = static_cast<QGyroscope *>(sensor)->reading();
            values.insert("x", reading->x());
            values.insert("y", reading->y());
            values.insert("z", reading->z());
        } else if (sensorName == "magnetometer") {
            QMagnetometerReading *reading = static_cast<QMagnetometer *>(sensor)->reading();
            values.insert("x", reading->x());
            values.insert("y", reading->y());
            values.insert("z", reading->z());
            values.insert("calibration_level", reading->calibrationLevel());
        } else if (sensorName == "compass") {
            QCompassReading *reading = static_cast<QCompass *>(sensor)->reading();
            values.insert("azimuth", reading->azimuth());
            values.insert("calibration_level", reading->calibrationLevel());
        } else if (sensorName == "orientation") {
            QOrientationReading *reading = static_cast<QOrientationSensor *>(sensor)->reading();
            values.insert("orientation", static_cast<int>(reading->orientation()));
        } else if (sensorName == "light") {
            QLightReading *reading = static_cast<QLightSensor *>(sensor)->reading();
            values.insert("lux", reading->lux());
        } else if (sensorName == "proximity") {
            QProximityReading *reading = static_cast<QProximitySensor *>(sensor)->reading();
            values.insert("close", reading->close());
        } else if (sensorName == "pressure") {
            QPressureReading *reading = static_cast<QPressureSensor *>(sensor)->reading();
            values.insert("pressure", reading->pressure());
        } else if (sensorName == "rotation") {
            QRotationReading *reading = static_cast<QRotationSensor *>(sensor)->reading();
            values.insert("x", reading->x());
            values.insert("y", reading->y());
            values.insert("z", reading->z());
        } else {
            return;
        }

        QJsonObject event;
        event.insert("type", "reading");
        event.insert("sensor", sensorName);
        event.insert("timestamp_us", static_cast<qint64>(sensor->reading()->timestamp()));
        event.insert("values", values);

        QHash<QTcpSocket *, ClientState>::const_iterator client = m_clients.constBegin();
        while (client != m_clients.constEnd()) {
            if (client.value().authenticated && client.value().subscriptions.contains(sensorName)) {
                client.key()->write(encode(event));
            }
            ++client;
        }
    }

private:
    struct ClientState {
        QByteArray buffer;
        bool authenticated;
        QSet<QString> subscriptions;

        ClientState() : authenticated(false) {}
    };

    void addSensor(const QString &name, QSensor *sensor)
    {
        m_sensors.insert(name, sensor);
        m_sensorNames.insert(sensor, name);
        connect(sensor, SIGNAL(readingChanged()), this, SLOT(publishReading()));
    }

    void handleMessage(QTcpSocket *socket, const QByteArray &line)
    {
        QJsonParseError parseError;
        const QJsonDocument document = QJsonDocument::fromJson(line, &parseError);
        if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
            fail(socket, "message must be a JSON object");
            return;
        }

        const QJsonObject message = document.object();
        ClientState &client = m_clients[socket];
        if (!client.authenticated) {
            if (message.value("type").toString() != "hello" ||
                    message.value("protocol").toInt() != kProtocolVersion ||
                    message.value("token").toString() != m_token) {
                fail(socket, "authentication failed");
                return;
            }
            client.authenticated = true;
            QJsonArray sensorNames;
            QHash<QString, QSensor *>::const_iterator sensor = m_sensors.constBegin();
            while (sensor != m_sensors.constEnd()) {
                sensorNames.append(sensor.key());
                ++sensor;
            }
            QJsonObject response;
            response.insert("type", "hello");
            response.insert("protocol", kProtocolVersion);
            response.insert("sensors", sensorNames);
            socket->write(encode(response));
            return;
        }

        if (message.value("type").toString() != "subscribe" || !message.value("sensors").isArray()) {
            fail(socket, "expected a subscribe message");
            return;
        }

        QSet<QString> requested;
        const QJsonArray names = message.value("sensors").toArray();
        for (int index = 0; index < names.size(); ++index) {
            const QString name = names.at(index).toString();
            if (m_sensors.contains(name)) {
                requested.insert(name);
            }
        }
        client.subscriptions = requested;

        QJsonArray subscribed;
        QSet<QString>::const_iterator name = client.subscriptions.constBegin();
        while (name != client.subscriptions.constEnd()) {
            subscribed.append(*name);
            ++name;
        }
        QJsonObject response;
        response.insert("type", "subscribed");
        response.insert("sensors", subscribed);
        socket->write(encode(response));
        // Starting a SensorFW backend can emit a reading synchronously. Queue
        // the acknowledgement first so protocol clients always observe it
        // before the first reading event.
        reconcileSensors();
    }

    void reconcileSensors()
    {
        QHash<QString, QSensor *>::iterator entry = m_sensors.begin();
        while (entry != m_sensors.end()) {
            bool needed = false;
            QHash<QTcpSocket *, ClientState>::const_iterator client = m_clients.constBegin();
            while (client != m_clients.constEnd()) {
                if (client.value().subscriptions.contains(entry.key())) {
                    needed = true;
                    break;
                }
                ++client;
            }

            if (needed && !entry.value()->isActive()) {
                entry.value()->setDataRate(m_dataRate);
                entry.value()->start();
            } else if (!needed && entry.value()->isActive()) {
                entry.value()->stop();
            }
            ++entry;
        }
    }

    void fail(QTcpSocket *socket, const QString &message)
    {
        socket->write(encode(errorMessage(message)));
        socket->disconnectFromHost();
        m_clients.remove(socket);
        reconcileSensors();
    }

    QString m_token;
    int m_dataRate;
    QHash<QString, QSensor *> m_sensors;
    QHash<QSensor *, QString> m_sensorNames;
    QHash<QTcpSocket *, ClientState> m_clients;
};

class LocationBridge : public QTcpServer
{
    Q_OBJECT

public:
    LocationBridge(const QString &token, QObject *parent = 0)
        : QTcpServer(parent), m_token(token), m_source(QGeoPositionInfoSource::createDefaultSource(this)), m_running(false)
    {
        if (m_source) {
            m_source->setUpdateInterval(1000);
            connect(m_source, SIGNAL(positionUpdated(QGeoPositionInfo)),
                    this, SLOT(publishPosition(QGeoPositionInfo)));
        }
    }

    bool hasPositionSource() const
    {
        return m_source != 0;
    }

protected:
    void incomingConnection(qintptr socketDescriptor)
    {
        QTcpSocket *socket = new QTcpSocket(this);
        if (!socket->setSocketDescriptor(socketDescriptor)) {
            qWarning() << "Could not accept location connection:" << socket->errorString();
            socket->deleteLater();
            return;
        }

        m_clients.insert(socket, ClientState());
        connect(socket, SIGNAL(readyRead()), this, SLOT(readClient()));
        connect(socket, SIGNAL(disconnected()), this, SLOT(removeClient()));
    }

private slots:
    void readClient()
    {
        QTcpSocket *socket = qobject_cast<QTcpSocket *>(sender());
        if (!socket || !m_clients.contains(socket)) {
            return;
        }

        ClientState &client = m_clients[socket];
        client.buffer.append(socket->readAll());
        if (client.buffer.size() > kMaximumLineBytes) {
            fail(socket, "message exceeds 64 KiB");
            return;
        }

        int newline = -1;
        while ((newline = client.buffer.indexOf('\n')) >= 0) {
            const QByteArray line = client.buffer.left(newline);
            client.buffer.remove(0, newline + 1);
            if (line.isEmpty()) {
                continue;
            }
            handleMessage(socket, line);
            if (!m_clients.contains(socket)) {
                return;
            }
        }
    }

    void removeClient()
    {
        QTcpSocket *socket = qobject_cast<QTcpSocket *>(sender());
        if (!socket) {
            return;
        }
        m_clients.remove(socket);
        socket->deleteLater();
        reconcileSource();
    }

    void publishPosition(const QGeoPositionInfo &position)
    {
        const QGeoCoordinate coordinate = position.coordinate();
        const QDateTime timestamp = position.timestamp().toUTC();
        if (!coordinate.isValid() || !timestamp.isValid() ||
                !std::isfinite(coordinate.latitude()) || !std::isfinite(coordinate.longitude())) {
            return;
        }

        const qint64 timestampUtcMs = timestamp.toMSecsSinceEpoch();
        if (timestampUtcMs <= 0) {
            return;
        }

        QJsonObject values;
        values.insert("latitude", coordinate.latitude());
        values.insert("longitude", coordinate.longitude());
        if (coordinate.type() == QGeoCoordinate::Coordinate3D && std::isfinite(coordinate.altitude())) {
            values.insert("altitude_m", coordinate.altitude());
        }
        addAttribute(position, QGeoPositionInfo::HorizontalAccuracy, "accuracy_m", &values);
        addAttribute(position, QGeoPositionInfo::GroundSpeed, "speed_mps", &values);
        addAttribute(position, QGeoPositionInfo::Direction, "bearing_degrees", &values);
        addAttribute(position, QGeoPositionInfo::VerticalSpeed, "vertical_speed_mps", &values);

        QJsonObject event;
        event.insert("type", "reading");
        event.insert("sensor", "location");
        event.insert("timestamp_utc_ms", timestampUtcMs);
        event.insert("values", values);

        QHash<QTcpSocket *, ClientState>::const_iterator client = m_clients.constBegin();
        while (client != m_clients.constEnd()) {
            if (client.value().authenticated && client.value().subscribed) {
                client.key()->write(encode(event));
            }
            ++client;
        }
    }

private:
    struct ClientState {
        QByteArray buffer;
        bool authenticated;
        bool subscribed;

        ClientState() : authenticated(false), subscribed(false) {}
    };

    static void addAttribute(const QGeoPositionInfo &position, QGeoPositionInfo::Attribute attribute,
                             const char *name, QJsonObject *values)
    {
        if (!position.hasAttribute(attribute)) {
            return;
        }
        const qreal value = position.attribute(attribute);
        if (std::isfinite(value)) {
            values->insert(QLatin1String(name), value);
        }
    }

    void handleMessage(QTcpSocket *socket, const QByteArray &line)
    {
        QJsonParseError parseError;
        const QJsonDocument document = QJsonDocument::fromJson(line, &parseError);
        if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
            fail(socket, "message must be a JSON object");
            return;
        }

        const QJsonObject message = document.object();
        ClientState &client = m_clients[socket];
        if (!client.authenticated) {
            if (message.value("type").toString() != "hello" ||
                    message.value("protocol").toInt() != kProtocolVersion ||
                    message.value("token").toString() != m_token) {
                fail(socket, "authentication failed");
                return;
            }
            client.authenticated = true;
            QJsonArray sources;
            if (m_source) {
                sources.append("location");
            }
            QJsonObject response;
            response.insert("type", "hello");
            response.insert("protocol", kProtocolVersion);
            response.insert("sensors", sources);
            socket->write(encode(response));
            return;
        }

        if (message.value("type").toString() != "subscribe" || !message.value("sensors").isArray()) {
            fail(socket, "expected a subscribe message");
            return;
        }

        client.subscribed = false;
        const QJsonArray names = message.value("sensors").toArray();
        for (int index = 0; index < names.size(); ++index) {
            if (m_source && names.at(index).toString() == "location") {
                client.subscribed = true;
                break;
            }
        }

        QJsonArray subscribed;
        if (client.subscribed) {
            subscribed.append("location");
        }
        QJsonObject response;
        response.insert("type", "subscribed");
        response.insert("sensors", subscribed);
        socket->write(encode(response));
        // Position sources may emit synchronously when updates are started.
        // The acknowledgement is queued first for the same reason as sensors.
        reconcileSource();
    }

    void reconcileSource()
    {
        bool needed = false;
        QHash<QTcpSocket *, ClientState>::const_iterator client = m_clients.constBegin();
        while (client != m_clients.constEnd()) {
            if (client.value().subscribed) {
                needed = true;
                break;
            }
            ++client;
        }

        if (needed && !m_running && m_source) {
            m_source->startUpdates();
            m_running = true;
        } else if (!needed && m_running && m_source) {
            m_source->stopUpdates();
            m_running = false;
        }
    }

    void fail(QTcpSocket *socket, const QString &message)
    {
        socket->write(encode(errorMessage(message)));
        socket->disconnectFromHost();
        m_clients.remove(socket);
        reconcileSource();
    }

    QString m_token;
    QGeoPositionInfoSource *m_source;
    bool m_running;
    QHash<QTcpSocket *, ClientState> m_clients;
};

int main(int argc, char *argv[])
{
    QCoreApplication application(argc, argv);
    QCoreApplication::setApplicationName("sailfish-sensor-bridge");

    QCommandLineParser parser;
    parser.setApplicationDescription("Publish Sailfish SensorFW and location readings to authenticated TCP clients.");
    parser.addHelpOption();
    parser.addOption(QCommandLineOption("listen", "Address to listen on.", "address", "127.0.0.1"));
    parser.addOption(QCommandLineOption("port", "TCP port to listen on.", "port", "8765"));
    parser.addOption(QCommandLineOption("gps-listen", "Address for the location listener.", "address", "127.0.0.1"));
    parser.addOption(QCommandLineOption("gps-port", "TCP port for the location listener.", "port", "8766"));
    parser.addOption(QCommandLineOption("token", "Required shared secret (at least 16 characters).", "token"));
    parser.addOption(QCommandLineOption("token-file", "File containing the required shared secret.", "path"));
    parser.addOption(QCommandLineOption("rate", "Requested sensor rate in Hz (1-200).", "hz", "50"));
    parser.process(application);

    QString token = parser.value("token");
    if (!parser.value("token-file").isEmpty()) {
        if (!token.isEmpty()) {
            QTextStream(stderr) << "Use either --token or --token-file.\n";
            return 2;
        }
        QFile tokenFile(parser.value("token-file"));
        if (!tokenFile.open(QIODevice::ReadOnly | QIODevice::Text)) {
            QTextStream(stderr) << "Could not read token file.\n";
            return 2;
        }
        token = QString::fromUtf8(tokenFile.readAll()).trimmed();
    }
    bool portOk = false;
    const int port = parser.value("port").toInt(&portOk);
    bool gpsPortOk = false;
    const int gpsPort = parser.value("gps-port").toInt(&gpsPortOk);
    bool rateOk = false;
    const int rate = parser.value("rate").toInt(&rateOk);
    const QHostAddress address(parser.value("listen"));
    const QHostAddress gpsAddress(parser.value("gps-listen"));
    if (token.length() < 16 || !portOk || port < 1 || port > 65535 ||
            !gpsPortOk || gpsPort < 1 || gpsPort > 65535 || !rateOk || rate < 1 || rate > 200 ||
            address.isNull() || gpsAddress.isNull()) {
        QTextStream(stderr) << "A token of at least 16 characters, valid addresses/ports, and a rate from 1 to 200 are required.\n";
        return 2;
    }

    SensorBridge bridge(token, rate);
    if (!bridge.listen(address, static_cast<quint16>(port))) {
        QTextStream(stderr) << "Could not listen: " << bridge.errorString() << '\n';
        return 1;
    }
    LocationBridge locationBridge(token);
    if (!locationBridge.listen(gpsAddress, static_cast<quint16>(gpsPort))) {
        QTextStream(stderr) << "Could not listen for location: " << locationBridge.errorString() << '\n';
        return 1;
    }
    qInfo() << "Listening on" << bridge.serverAddress().toString() << bridge.serverPort();
    qInfo() << "Location listener on" << locationBridge.serverAddress().toString()
            << locationBridge.serverPort() << "source available:" << locationBridge.hasPositionSource();
    return application.exec();
}

#include "sailfish-sensor-bridge.moc"
