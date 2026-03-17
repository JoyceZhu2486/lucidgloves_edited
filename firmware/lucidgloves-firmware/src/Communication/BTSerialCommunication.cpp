#include "BTSerialCommunication.h"

#if COMMUNICATION == COMM_BTSERIAL

// ─── ServerCallbacks ────────────────────────────────────────────────────────

void BTSerialCommunication::ServerCallbacks::onConnect(BLEServer* pServer) {
    parent->m_clientConnected = true;
    #if BT_ECHO
    Serial.println("[BLE] Client connected");
    #endif
}

void BTSerialCommunication::ServerCallbacks::onDisconnect(BLEServer* pServer) {
    parent->m_clientConnected = false;
    #if BT_ECHO
    Serial.println("[BLE] Client disconnected — restarting advertising");
    #endif
    // Restart advertising so the host can reconnect without rebooting
    pServer->getAdvertising()->start();
}

// ─── RxCallbacks ────────────────────────────────────────────────────────────

void BTSerialCommunication::RxCallbacks::onWrite(BLECharacteristic* pChar) {
    String val = pChar->getValue();
    if (val.length() > 0 && val.length() < sizeof(parent->m_rxBuf)) {
        val.toCharArray(parent->m_rxBuf, sizeof(parent->m_rxBuf));
        parent->m_rxReady = true;
    }
}

// ─── BTSerialCommunication ───────────────────────────────────────────────────

BTSerialCommunication::BTSerialCommunication()
    : m_isOpen(false), m_clientConnected(false),
      m_pServer(nullptr), m_pTxChar(nullptr), m_pRxChar(nullptr),
      m_rxReady(false)
{
    m_rxBuf[0] = '\0';
    m_serverCB.parent = this;
    m_rxCB.parent     = this;
}

bool BTSerialCommunication::isOpen() {
    // Accept data as soon as BLE stack is up;
    // output() checks m_clientConnected before notifying.
    return m_isOpen;
}

void BTSerialCommunication::start() {
    #if BT_ECHO
    Serial.begin(SERIAL_BAUD_RATE);
    Serial.println("[BLE] Initialising...");
    #endif

    BLEDevice::init(BTSERIAL_DEVICE_NAME);

    m_pServer = BLEDevice::createServer();
    m_pServer->setCallbacks(&m_serverCB);

    // Create Nordic UART Service
    BLEService* pService = m_pServer->createService(NUS_SERVICE_UUID);

    // TX characteristic — ESP32 sends data to host via notifications
    m_pTxChar = pService->createCharacteristic(
        NUS_TX_CHAR_UUID,
        BLECharacteristic::PROPERTY_NOTIFY
    );
    m_pTxChar->addDescriptor(new BLE2902());  // enables notifications on client

    // RX characteristic — host writes data to ESP32
    m_pRxChar = pService->createCharacteristic(
        NUS_RX_CHAR_UUID,
        BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR
    );
    m_pRxChar->setCallbacks(&m_rxCB);

    pService->start();

    // Advertise with the NUS service UUID so hosts can filter by service
    BLEAdvertising* pAdv = BLEDevice::getAdvertising();
    pAdv->addServiceUUID(NUS_SERVICE_UUID);
    pAdv->setScanResponse(true);
    pAdv->setMinPreferred(0x06);  // helps with iPhone connection stability
    pAdv->setMinPreferred(0x12);
    BLEDevice::startAdvertising();

    m_isOpen = true;

    #if BT_ECHO
    Serial.println("[BLE] Advertising as: " BTSERIAL_DEVICE_NAME);
    #endif
}

void BTSerialCommunication::output(char* data) {
    if (!m_clientConnected) return;  // drop silently if nobody connected yet

    m_pTxChar->setValue((uint8_t*)data, strlen(data));
    m_pTxChar->notify();

    #if BT_ECHO
    Serial.print(data);
    #endif
}

bool BTSerialCommunication::readData(char* input) {
    if (!m_rxReady) return false;
    strcpy(input, m_rxBuf);
    m_rxReady = false;
    m_rxBuf[0] = '\0';
    return strlen(input) > 0;
}

#endif // COMMUNICATION == COMM_BTSERIAL
