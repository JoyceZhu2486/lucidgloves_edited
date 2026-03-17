#pragma once
#include "ICommunication.h"
#include "../../Config.h"

#if COMMUNICATION == COMM_BTSERIAL
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

// Nordic UART Service (NUS) UUIDs — recognized natively by macOS, iOS, Android
#define NUS_SERVICE_UUID        "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
#define NUS_RX_CHAR_UUID        "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"  // Phone writes here
#define NUS_TX_CHAR_UUID        "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"  // ESP32 notifies here

class BTSerialCommunication : public ICommunication {
private:
    bool m_isOpen;
    bool m_clientConnected;

    BLEServer*          m_pServer;
    BLECharacteristic*  m_pTxChar;   // ESP32 → host (notify)
    BLECharacteristic*  m_pRxChar;   // host → ESP32 (write)

    // Received data buffer
    char    m_rxBuf[256];
    bool    m_rxReady;

    // BLE callbacks declared as inner classes so they can access private members
    class ServerCallbacks : public BLEServerCallbacks {
    public:
        BTSerialCommunication* parent;
        void onConnect(BLEServer* pServer) override;
        void onDisconnect(BLEServer* pServer) override;
    };

    class RxCallbacks : public BLECharacteristicCallbacks {
    public:
        BTSerialCommunication* parent;
        void onWrite(BLECharacteristic* pChar) override;
    };

    ServerCallbacks m_serverCB;
    RxCallbacks     m_rxCB;

public:
    BTSerialCommunication();

    bool isOpen()               override;
    void start()                override;
    void output(char* data)     override;
    bool readData(char* input)  override;
};

#endif // COMMUNICATION == COMM_BTSERIAL
