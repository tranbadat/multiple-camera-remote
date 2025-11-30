package vn.com.multicamsynceclient

import android.Manifest
import android.os.Bundle
import android.util.Log
import android.view.Surface.ROTATION_90
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.Socket
import java.net.SocketTimeoutException
import java.nio.ByteBuffer
import java.util.concurrent.Executors
import androidx.camera.core.ImageAnalysis
import androidx.core.content.edit

private const val ACTION_TYPE = "type"

private const val DEVICE_ID = "deviceId"

private const val TOKEN = "token"

private const val NAME = "name"

private const val SYNC_START = "SYNC_START"

private const val SYNC_STOP = "SYNC_STOP"

private const val CMD_REGISTER = "REGISTER"

private const val CMD_CONNECT = "CONNECT"

private const val CMD_START = "START"

private const val CMD_STOP = "STOP"

private const val FRAME_DELAY = 200L  // 200ms ~ 5 FPS

class MainActivity : ComponentActivity() {

    // --- Cấu hình ---
    private var serverIp = "172.19.0.1"
    private var token = "123456"
    private var camName = ""
    private val controlPort = 5000   // UDP control port (trùng server)

    private var isRecording = false
//    private var imageCapture: ImageCapture? = null
    private lateinit var previewView: androidx.camera.view.PreviewView
    private lateinit var logView: TextView
    private lateinit var deviceListView: TextView   // NEW: view hiển thị danh sách devices

    private var tcpSocket: Socket? = null
    private var udpSocket: DatagramSocket? = null
    private val job = SupervisorJob()
    private val coroutineScope = CoroutineScope(Dispatchers.IO + job)
    private val cameraExecutor = Executors.newSingleThreadExecutor()

    private var lastSentTime = 0L


    // deviceId của máy này (random đơn giản, bạn có thể thay bằng Android ID, v.v.)
    private lateinit var deviceId: String

    // Thông tin device do server trả về
    data class DeviceInfo(
        val deviceId: String,
        val name: String,
        val port: Int,
        val subdir: String
    )

    private var myDeviceInfo: DeviceInfo? = null
    private var deviceList: List<DeviceInfo> =
        emptyList()   // NEW: lưu toàn bộ danh sách server trả về

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        // Lấy hoặc tạo deviceId
        deviceId = getOrCreateDeviceId()

        // Lấy giá trị mặc định từ strings.xml
        serverIp = getString(R.string.server_ip)
        token = getString(R.string.default_token)
        camName = getString(R.string.camera_id)

        previewView = findViewById(R.id.previewView)
        val ipInput = findViewById<EditText>(R.id.ipInput)
        val tokenInput = findViewById<EditText>(R.id.tokenInput)
        val camInputId = findViewById<TextView>(R.id.camId)

        logView = findViewById(R.id.logView)
        deviceListView = findViewById(R.id.devicesListView)  // NEW: bind TextView list devices

        ipInput.setText(serverIp)
        tokenInput.setText(token)

        fun log(msg: String) {
            runOnUiThread { logView.append("$msg\n") }
        }
        log("Current deviceId: $deviceId")
        // xin quyền camera
        val permissionLauncher =
            registerForActivityResult(ActivityResultContracts.RequestPermission()) {
                if (it) startCamera()
            }
        permissionLauncher.launch(Manifest.permission.CAMERA)

        // NÚT CONNECT
        findViewById<Button>(R.id.btnConnect).setOnClickListener {
            val ipText = ipInput.text.toString().trim()
            if (ipText.isNotEmpty()) serverIp = ipText

            val tokenText = tokenInput.text.toString().trim()
            if (tokenText.isNotEmpty()) token = tokenText

            val camText = camInputId.text.toString().trim()
            if (camText.isNotEmpty()) {
                camName = camText
            } else {
                camName = "Camera_$deviceId"
                camInputId.text = camName
            }

            coroutineScope.launch {
                // 1. Handshake CONNECT (UDP) -> nhận danh sách devices
                val list = performConnectHandshake(::log)

                if (list.isNullOrEmpty()) {
                    Log.i("MainActivity", "CONNECT FAILED: no device list received.")
                    log("CONNECT FAILED: không nhận được danh sách device.")
                    withContext(Dispatchers.Main) {
                        Toast.makeText(
                            this@MainActivity,
                            "CONNECT FAILED",
                            Toast.LENGTH_SHORT
                        ).show()
                    }
                    return@launch
                }

                // 2. Tìm device của chính mình (deviceId hiện tại)
                val my = list.find { it.deviceId == deviceId } ?: list.first()
                myDeviceInfo = my
                deviceList = list   // NEW: lưu lại toàn bộ
                Log.i(
                    "MainActivity",
                    "My device info: id=${my.deviceId}, name=${my.name}, port=${my.port}, dir=${my.subdir}"
                )
                log("My device info: id=${my.deviceId}, name=${my.name}, port=${my.port}, dir=${my.subdir}")
                camInputId.text = my.name
                withContext(Dispatchers.Main) {
                    Toast.makeText(
                        this@MainActivity,
                        "Assigned port ${my.port}, dir ${my.subdir}",
                        Toast.LENGTH_SHORT
                    ).show()
                    Log.i("MainActivity", "Device list: ${deviceList.size} devices shown.")
                }

                // 3. Bắt đầu lắng nghe UDP trong MỘT COROUTINE RIÊNG
                launch {
                    startUdpListener(::log)
                }

                // 4. REGISTER – CỰC KỲ QUAN TRỌNG
                sendUdpCommand(CMD_REGISTER, ::log)
                // 5. Kết nối TCP tới port được gán
                connectTcp(my.port, ::log)

            }
        }

        findViewById<Button>(R.id.btnStart).setOnClickListener {
            sendUdpCommand(CMD_START, ::log)
        }
        findViewById<Button>(R.id.btnStop).setOnClickListener {
            sendUdpCommand(CMD_STOP, ::log)
        }
    }

    //------------------ GET OR CREATE DEVICE ID ------------------
    private fun getOrCreateDeviceId(): String {
        val prefs = getSharedPreferences("camera_prefs", MODE_PRIVATE)
        val existing = prefs.getString("device_id", null)

        return if (existing != null) {
            // Thiết bị đã từng connect, dùng lại id cũ
            existing
        } else {
            // Thiết bị mới, tạo ngẫu nhiên và lưu lại
            val newId = "android_" + (10000..99999).random()
            prefs.edit { putString("device_id", newId) }
            newId
        }
    }


    // ------------------ CAMERA ------------------

    private fun startCamera() {
        val providerFuture = ProcessCameraProvider.getInstance(this)
        providerFuture.addListener({
            val provider = providerFuture.get()
            val preview = Preview.Builder().build()
            preview.setSurfaceProvider(previewView.surfaceProvider)

            val imageAnalyzer = ImageAnalysis.Builder()
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_YUV_420_888)
                .build()
            imageAnalyzer.setAnalyzer(cameraExecutor) { image ->
                if (isRecording) {
                    val now = System.currentTimeMillis()
                    if (now - lastSentTime >= FRAME_DELAY) {
                        lastSentTime = now
                        // YUV_420_888 to JPEG using YuvImage (NV21)
                        val yBuffer = image.planes[0].buffer
                        val uBuffer = image.planes[1].buffer
                        val vBuffer = image.planes[2].buffer

                        val ySize = yBuffer.remaining()
                        val uSize = uBuffer.remaining()
                        val vSize = vBuffer.remaining()

                        val nv21 = ByteArray(ySize + uSize + vSize)
                        yBuffer.get(nv21, 0, ySize)
                        vBuffer.get(nv21, ySize, vSize)
                        uBuffer.get(nv21, ySize + vSize, uSize)

                        val yuvImage = android.graphics.YuvImage(
                            nv21,
                            android.graphics.ImageFormat.NV21,
                            image.width,
                            image.height,
                            null
                        )
                        val baos = ByteArrayOutputStream()
                        yuvImage.compressToJpeg(
                            android.graphics.Rect(0, 0, image.width, image.height),
                            50,
                            baos
                        )
                        val jpeg = baos.toByteArray()
                        sendTcpFrame(jpeg)
                    }
                }
                image.close()
            }

            provider.unbindAll()
            provider.bindToLifecycle(
                this,
                CameraSelector.DEFAULT_BACK_CAMERA,
                preview,
                imageAnalyzer
            )
        }, ContextCompat.getMainExecutor(this))
    }


    private fun sendTcpFrame(jpeg: ByteArray) {
        try {
            val os = tcpSocket?.getOutputStream() ?: return
            val header = ByteBuffer.allocate(4).putInt(jpeg.size).array()
            os.write(header)
            os.write(jpeg)
            os.flush()
        } catch (e: Exception) {
            e.stackTrace.toString();
        }
    }
    // ------------------ UI hiển thị danh sách devices (NEW) ------------------

    private fun updateDeviceListUI(
        devices: List<DeviceInfo>,
        me: DeviceInfo?,
        tv: TextView
    ) {
        val sb = StringBuilder()
        sb.append("Devices from server:\n\n")
        for (d in devices) {
            val isMe = (me != null && d.deviceId == me.deviceId)
            if (isMe) sb.append("★ ") else sb.append("• ")

            sb.append("deviceId=${d.deviceId}\n")
            sb.append("   name=${d.name}\n")
            sb.append("   port=${d.port}\n")
            sb.append("   folder=${d.subdir}\n")
            if (isMe) {
                sb.append("   >>> THIS DEVICE <<<\n")
            }
            sb.append("\n")
        }
        tv.text = sb.toString()
    }

    // ------------------ CONNECT HANDSHAKE (UDP) ------------------

    private suspend fun performConnectHandshake(log: (String) -> Unit): List<DeviceInfo>? {
        return withContext(Dispatchers.IO) {
            try {
                // Tạo UDP socket nếu chưa có
                if (udpSocket == null || udpSocket!!.isClosed) {
                    udpSocket = DatagramSocket()
                    udpSocket!!.soTimeout = 5000  // timeout 5s cho handshake CONNECT
                }

                val jsonObject = JSONObject().apply {
                    put(ACTION_TYPE, CMD_CONNECT)
                    put(DEVICE_ID, deviceId)
                    put(TOKEN, token)
                    put(NAME, camName) // tên cam / device
                }

                val data = jsonObject.toString().toByteArray()
                val packet = DatagramPacket(
                    data,
                    data.size,
                    InetAddress.getByName(serverIp),
                    controlPort
                )

                udpSocket!!.send(packet)
                log("UDP → CONNECT (deviceId=$deviceId, name=$camName)")

                // Nhận JSON array từ server
                val buf = ByteArray(8192)
                val respPacket = DatagramPacket(buf, buf.size)

                try {
                    udpSocket!!.receive(respPacket)
                    // Nếu nhận được CONNECT response thì bỏ timeout, để listener chờ vô thời hạn
                    udpSocket!!.soTimeout = 0
                } catch (toe: SocketTimeoutException) {
                    log("CONNECT timeout: ${toe.message}")
                    return@withContext null
                }

                val msg = String(respPacket.data, 0, respPacket.length)
                Log.i("MainActivity", "CONNECT RESP: $msg")

                val arr = JSONArray(msg)
                val list = mutableListOf<DeviceInfo>()
                for (i in 0 until arr.length()) {
                    val o = arr.getJSONObject(i)
                    val dId = o.optString(DEVICE_ID)
                    val name = o.optString(NAME)
                    val port = o.optInt("port")
                    val subdir = o.optString("subdir")
                    if (dId.isNotEmpty() && port != 0) {
                        list.add(DeviceInfo(dId, name, port, subdir))
                    }
                }
                list
            } catch (e: Exception) {
                log("CONNECT ERR: ${e.message}")
                Log.i("MainActivity", "CONNECT ERR: ${e.message}")
                null
            }
        }
    }

    // ------------------ TCP ------------------

    private fun connectTcp(port: Int, log: (String) -> Unit) {
        try {
            tcpSocket?.close()
            tcpSocket = Socket(serverIp, port)
            log("TCP Connected to $serverIp:$port")
            Log.i("MainActivity", "TCP Connected to $serverIp:$port")
            runOnUiThread {
                Toast.makeText(
                    this,
                    "TCP connected to $serverIp:$port",
                    Toast.LENGTH_SHORT
                ).show()
            }
        } catch (e: Exception) {
            Log.i("MainActivity", "TCP ERROR: ${e.message}")
            log("TCP ERROR: ${e.message}")
            runOnUiThread {
                Toast.makeText(
                    this,
                    "TCP ERROR: ${e.message}",
                    Toast.LENGTH_SHORT
                ).show()
            }
        }
    }

    // ------------------ UDP ------------------

    private suspend fun startUdpListener(log: (String) -> Unit) {
        // Đảm bảo đã có udpSocket (nếu chưa thì tạo)
        val socket = withContext(Dispatchers.IO) {
            if (udpSocket == null || udpSocket!!.isClosed) {
                udpSocket = DatagramSocket()
            }
            udpSocket!!
        }

        try {
            val buf = ByteArray(4096)
            while (true) {
                val packet = DatagramPacket(buf, buf.size)

                // receive trên cùng socket
                withContext(Dispatchers.IO) {
                    socket.receive(packet)
                }

                val msg = String(packet.data, 0, packet.length).trim()
                log("UDP Receiver: $msg")

                runOnUiThread {
                    when (msg) {
                        SYNC_START -> Toast.makeText(
                            this,
                            "Received START command",
                            Toast.LENGTH_SHORT
                        ).show()

                        SYNC_STOP -> Toast.makeText(
                            this,
                            "Received STOP command",
                            Toast.LENGTH_SHORT
                        ).show()
                    }
                }
                Log.i("MainActivity", "UDP Receiver: $msg")
                when (msg) {
                    SYNC_START -> startRecording(log)
                    SYNC_STOP -> stopRecording(log)
                }
            }
        } catch (e: Exception) {
            log("UDP Listen Error: ${e.message}")
            Log.i("MainActivity", "UDP Listen Error: ${e.message}");
        }
    }

    private fun sendUdpCommand(cmd: String, log: (String) -> Unit) {
        //Stop capture if needed
        coroutineScope.launch {
            try {
                if (udpSocket == null || udpSocket!!.isClosed) {
                    udpSocket = DatagramSocket()
                }

                val jsonObject = JSONObject().apply {
                    put(ACTION_TYPE, cmd)
                    put(DEVICE_ID, deviceId)
                    put(TOKEN, token)
                }
                val data = jsonObject.toString().toByteArray()
                val packet = DatagramPacket(
                    data,
                    data.size,
                    InetAddress.getByName(serverIp),
                    controlPort
                )

                udpSocket!!.send(packet)
                log("UDP → $cmd")
            } catch (e: Exception) {
                log("UDP ERR: ${e.message}")
            }
        }
    }

    // ------------------ RECORDING / FRAME SENDING ------------------

    private fun startRecording(log: (String) -> Unit) {
        if (isRecording) return
        isRecording = true
        Log.i("MainActivity", "START RECORDING")
        log(">>> START RECORDING <<<")
//        coroutineScope.launch {
//            while (isRecording) {
//                sendFrame(log)
//                delay(FRAME_DELAY) // ~5 FPS
//            }
//        }
    }

    private fun stopRecording(log: (String) -> Unit) {
        isRecording = false
        lastSentTime = 0L
        log(">>> STOP RECORDING <<<")
        Log.i("MainActivity", "STOP RECORDING")
    }

//    private fun sendFrame(log: (String) -> Unit) {
//        Log.i("MainActivity", "Capturing frame...")
//        val imgCap = imageCapture ?: return
//
//        imgCap.takePicture(cameraExecutor, object : ImageCapture.OnImageCapturedCallback() {
//            override fun onCaptureSuccess(img: ImageProxy) {
//                try {
//                    val buffer = img.planes[0].buffer
//                    val bytes = ByteArray(buffer.remaining())
//                    buffer.get(bytes)
//                    val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
//
//                    val stream = ByteArrayOutputStream()
//                    bitmap.compress(android.graphics.Bitmap.CompressFormat.JPEG, 50, stream)
//                    val jpeg = stream.toByteArray()
//
//                    val base64Str = Base64.encodeToString(jpeg, Base64.NO_WRAP)
//                    val base64Bytes = base64Str.toByteArray(Charsets.UTF_8)
//
//                    tcpSocket?.let { socket ->
//                        if (socket.isConnected) {
//                            val os = socket.getOutputStream()
//
//                            val header = ByteBuffer.allocate(4).putInt(base64Bytes.size).array()
//                            os.write(header)
//                            os.write(base64Bytes)
//                            os.flush()
//
//                            Log.i("MainActivity", "Sent frame: ${base64Bytes.size} bytes")
//                        } else {
//                            log("TCP not connected")
//                        }
//                    } ?: {
//                        log("TCP socket is null")
//                        Log.i("MainActivity", "TCP socket is null")
//                    }
//                } catch (e: Exception) {
//                    Log.i("MainActivity", "TCP SEND ERR: ${e.message}")
//                    log("TCP SEND ERR: ${e.message}")
//                } finally {
//                    img.close()
//                }
//            }
//
//            override fun onError(exc: ImageCaptureException) {
//                log("Capture error: ${exc.message}")
//            }
//        })
//    }

    override fun onDestroy() {
        super.onDestroy()

        // 1. Dừng loop gửi frame
        isRecording = false

        // 2. Hủy tất cả coroutine còn chạy
        job.cancel()

        // 3. Đóng TCP socket
        try {
            tcpSocket?.close()
        } catch (_: Exception) {
        } finally {
            tcpSocket = null
        }

        // 4. Đóng UDP socket (sẽ làm hàm receive() ném exception và thoát loop listen)
        try {
            udpSocket?.close()
        } catch (_: Exception) {
        } finally {
            udpSocket = null
        }

        // 5. Tắt camera executor
        try {
            cameraExecutor.shutdown()
        } catch (_: Exception) {
        }
    }

}
