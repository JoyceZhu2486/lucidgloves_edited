#include "BTSerialCommunication.h"
#include "esp_gap_ble_api.h"

#if COMMUNICATION == COMM_BTSERIAL

// ─── ServerCallbacks ────────────────────────────────────────────────────────

void BTSerialCommunication::ServerCallbacks::onConnect(BLEServer* pServer) {
    // Fallback — called on some core versions without param
    parent->m_clientConnected = true;
    #if BT_ECHO
    Serial.println("[BLE] Client connected (no params)");
    #endif
}

void BTSerialCommunication::ServerCallbacks::onConnect(
        BLEServer* pServer, esp_ble_gatts_cb_param_t* param) {

    parent->m_clientConnected = true;

    // Use peer address from connection param to update supervision timeout
    esp_ble_conn_update_params_t conn_params = {};
    memcpy(conn_params.bda, param->connect.remote_bda, sizeof(esp_bd_addr_t));
    conn_params.latency = 0;
    conn_params.max_int = 0x28;   // 50ms
    conn_params.min_int = 0x18;   // 30ms
    conn_params.timeout = 800;    // 8000ms supervision timeout

    esp_ble_gap_update_conn_params(&conn_params);

    #if BT_ECHO
    Serial.println("[BLE] Client connected — conn params updated");
    #endif
}

void BTSerialCommunication::ServerCallbacks::onDisconnect(BLEServer* pServer) {
    parent->m_clientConnected = false;
    #if BT_ECHO
    Serial.println("[BLE] Client disconnected — restarting advertising");
    #endif
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
    : m_isOpen(false), m_clientConnected(false), m_lastNotifyMs(0),
      m_pServer(nullptr), m_pTxChar(nullptr), m_pRxChar(nullptr),
      m_rxReady(false)
{
    m_rxBuf[0] = '\0';
    m_serverCB.parent = this;
    m_rxCB.parent     = this;
}

bool BTSerialCommunication::isOpen() {
    return m_isOpen;
}

void BTSerialCommunication::start() {
    Serial.begin(SERIAL_BAUD_RATE);  // always init for USB debug
    #if BT_ECHO
    Serial.println("[BLE] Initialising...");
    #endif

    BLEDevice::init(BTSERIAL_DEVICE_NAME);
    BLEDevice::setMTU(128);

    m_pServer = BLEDevice::createServer();
    m_pServer->setCallbacks(&m_serverCB);

    BLEService* pService = m_pServer->createService(NUS_SERVICE_UUID);

    // TX: ESP32 → host via notifications
    m_pTxChar = pService->createCharacteristic(
        NUS_TX_CHAR_UUID,
        BLECharacteristic::PROPERTY_NOTIFY
    );
    m_pTxChar->addDescriptor(new BLE2902());

    // RX: host → ESP32 via write
    m_pRxChar = pService->createCharacteristic(
        NUS_RX_CHAR_UUID,
        BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR
    );
    m_pRxChar->setCallbacks(&m_rxCB);

    pService->start();

    BLEAdvertising* pAdv = BLEDevice::getAdvertising();
    pAdv->addServiceUUID(NUS_SERVICE_UUID);
    pAdv->setScanResponse(true);
    pAdv->setMinPreferred(0x06);
    pAdv->setMinPreferred(0x12);
    BLEDevice::startAdvertising();

    m_isOpen = true;

    #if BT_ECHO
    Serial.println("[BLE] Advertising as: " BTSERIAL_DEVICE_NAME);
    #endif
}

void BTSerialCommunication::output(char* data) {
    if (!m_clientConnected) return;

    // Throttle notifications to max 50 Hz to avoid overwhelming BLE stack
    uint32_t now = millis();
    if (now - m_lastNotifyMs < 20) return;
    m_lastNotifyMs = now;

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