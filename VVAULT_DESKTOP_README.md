# VVAULT Desktop Application

A comprehensive desktop application for managing AI construct memory capsules with blockchain integration for immutable storage and verification.

## 🚀 Quick Start

### Prerequisites
- Python 3.7+
- Virtual environment (recommended)
- Internet connection for blockchain operations

### Installation

1. **Clone the repository** (if not already done):
   ```bash
   git clone <repository-url>
   cd VVAULT
   ```

2. **Create a virtual environment**:
   ```bash
   python3 -m venv vvault_env
   source vvault_env/bin/activate  # On Windows: vvault_env\Scripts\activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Start the desktop application**:
   ```bash
   python3 start_vvault_desktop.py
   ```

## 🏗️ Architecture

### Core Components

1. **VVAULT Launcher** (`vvault_launcher.py`)
   - Main application entry point
   - Integrated GUI with all components
   - System monitoring and status management

2. **Process Manager** (`process_manager.py`)
   - Secure process execution for brain.py
   - Real-time monitoring and health checks
   - Automatic restart capabilities
   - Resource usage tracking

3. **Capsule Viewer** (`capsule_viewer.py`)
   - Advanced capsule analysis and visualization
   - JSON schema validation
   - Memory analysis and personality trait visualization
   - Security filtering for sensitive data

4. **Security Layer** (`security_layer.py`)
   - Comprehensive security and threat detection
   - Sensitive data masking and encryption
   - Access control and authentication
   - Audit logging and compliance

5. **Blockchain Sync** (`blockchain_sync.py`)
   - Multi-blockchain synchronization
   - IPFS integration for decentralized storage
   - Transaction monitoring and verification
   - Cost optimization strategies

6. **Main GUI** (`vvault_gui.py`)
   - Primary user interface
   - System status and control
   - Real-time output monitoring
   - Capsule management interface

## 🎯 Features

### Main Control
- **System Status**: Real-time monitoring of VVAULT core process
- **Process Management**: Start, stop, and restart VVAULT core
- **Output Monitoring**: Live system output with filtering
- **System Check**: Comprehensive environment validation

### Capsule Management
- **Capsule Viewer**: Advanced JSON schema preview and validation
- **Memory Analysis**: Detailed memory categorization and analysis
- **Personality Visualization**: Trait visualization and MBTI analysis
- **Security Scanning**: Sensitive data detection and masking
- **Export Capabilities**: Safe capsule export with data masking

### Blockchain Integration
- **Multi-Blockchain Support**: Ethereum, Polygon, Arbitrum, Optimism, Base
- **IPFS Integration**: Decentralized storage for large capsule data
- **Transaction Monitoring**: Real-time transaction status and verification
- **Cost Optimization**: Multiple storage strategies for different use cases
- **Smart Contract Interaction**: Ethereum-based capsule registry

### Security Features
- **Threat Detection**: Real-time security monitoring and threat detection
- **Data Masking**: Automatic sensitive data detection and masking
- **Access Control**: User authentication and authorization
- **Audit Logging**: Comprehensive security event logging
- **Encryption**: Secure data encryption and key management

## 📱 User Interface

### Main Control Tab
- System status and health monitoring
- Process control (start, stop, restart)
- Real-time output display
- System check and validation

### Capsule Management Tab
- Capsule list with filtering and search
- Advanced capsule viewer with JSON preview
- Memory analysis and visualization
- Security scanning and masking
- Export and sharing capabilities

### Blockchain Sync Tab
- Multi-blockchain configuration
- Sync operations and monitoring
- Transaction history and verification
- Cost analysis and optimization
- IPFS integration status

### Security Tab
- Security status and monitoring
- Threat detection and alerts
- Security scan and reporting
- Access control management
- Audit log viewing and export

### System Status Tab
- System information and configuration
- Process status and resource usage
- Component health and status
- Performance metrics and statistics

## 🔧 Configuration

### Environment Setup
The application expects the following directory structure:
```
VVAULT/
├── vvault_env/              # Virtual environment
├── corefiles/
│   └── brain.py            # VVAULT core system
├── capsules/               # Capsule storage directory
├── vvault_launcher.py      # Main launcher
├── vvault_gui.py          # Main GUI
├── process_manager.py      # Process management
├── capsule_viewer.py       # Capsule viewer
├── security_layer.py      # Security layer
├── blockchain_sync.py     # Blockchain sync
└── start_vvault_desktop.py # Startup script
```

### Blockchain Configuration
The application supports multiple blockchain networks:
- **Ethereum Mainnet**: High security, higher costs
- **Polygon**: Lower costs, good security
- **Arbitrum**: Layer 2 scaling, reduced costs
- **Optimism**: Layer 2 scaling, reduced costs
- **Base**: Coinbase's Layer 2 solution

### Security Settings
- **Sensitive Data Masking**: Automatically mask private keys, hashes, and tokens
- **Access Control**: User authentication and permission management
- **Threat Detection**: Real-time monitoring for security threats
- **Audit Logging**: Comprehensive logging of all security events

## 🚀 Usage

### Starting the Application
```bash
# Simple startup
python3 start_vvault_desktop.py

# Or run the launcher directly
python3 vvault_launcher.py
```

### Basic Operations

1. **Launch VVAULT Core**:
   - Click "Launch VVAULT Core" in the Main Control tab
   - Monitor the output for any errors or warnings
   - Check the System Status tab for process information

2. **View Capsules**:
   - Navigate to the Capsule Management tab
   - Select a capsule from the list
   - View detailed information in the JSON, Analysis, or Security tabs

3. **Sync to Blockchain**:
   - Navigate to the Blockchain Sync tab
   - Select a blockchain network
   - Click "Connect" to establish connection
   - Use "Sync All Capsules" to upload capsules

4. **Security Monitoring**:
   - Navigate to the Security tab
   - Click "Security Scan" to perform a comprehensive scan
   - View the security report for detailed information

### Advanced Features

#### Capsule Analysis
- **Memory Analysis**: Detailed breakdown of memory types and content
- **Personality Visualization**: Trait visualization and MBTI analysis
- **Security Scanning**: Detection of sensitive data and security issues
- **Export Options**: Safe export with data masking

#### Blockchain Operations
- **Multi-Network Support**: Switch between different blockchain networks
- **Cost Optimization**: Choose storage strategies based on cost and security needs
- **Transaction Monitoring**: Real-time tracking of blockchain transactions
- **Verification**: Cryptographic verification of capsule integrity

#### Security Management
- **Threat Detection**: Real-time monitoring for security threats
- **Access Control**: Manage user permissions and restrictions
- **Audit Logging**: Comprehensive logging of all security events
- **Data Protection**: Automatic encryption and masking of sensitive data

## 🔒 Security Considerations

### Data Protection
- All sensitive data is automatically masked in logs and displays
- Encryption is used for secure data storage
- Access control prevents unauthorized operations
- Audit logging tracks all security-relevant events

### Network Security
- Secure connections to blockchain networks
- IPFS integration for decentralized storage
- Smart contract verification for immutable storage
- Transaction signing with secure key management

### System Security
- Process isolation for secure execution
- Resource monitoring to prevent abuse
- Automatic threat detection and response
- Comprehensive security event logging

## 🐛 Troubleshooting

### Common Issues

1. **Virtual Environment Not Found**:
   ```bash
   python3 -m venv vvault_env
   source vvault_env/bin/activate
   pip install -r requirements.txt
   ```

2. **Brain Script Not Found**:
   - Ensure `corefiles/brain.py` exists
   - Check the project directory structure

3. **Capsules Directory Missing**:
   - The application will create the directory automatically
   - Ensure proper permissions for directory creation

4. **Blockchain Connection Failed**:
   - Check internet connection
   - Verify blockchain network configuration
   - Ensure sufficient gas for transactions

### Debug Mode
Enable debug logging by setting the log level:
```python
import logging
logging.getLogger().setLevel(logging.DEBUG)
```

### Log Files
- Application logs: `vvault_desktop.log`
- Security logs: Available in the Security tab
- Process logs: Available in the Main Control tab

## 📚 API Reference

### Process Manager
```python
from process_manager import VVAULTProcessManager, ProcessConfig

# Create process manager
config = ProcessConfig(
    project_dir="/path/to/vvault",
    venv_activate="/path/to/venv/bin/activate",
    brain_script="/path/to/brain.py",
    working_directory="/path/to/working/dir"
)
manager = VVAULTProcessManager(config)

# Start process
success = manager.start_process()

# Get status
status = manager.get_status()
```

### Security Layer
```python
from security_layer import VVAULTSecurityLayer

# Create security layer
security = VVAULTSecurityLayer("/path/to/vvault")

# Authenticate user
success, session_id = security.authenticate_user("user_id", {"password": "password"})

# Authorize operation
authorized = security.authorize_operation(session_id, "operation", "resource")
```

### Capsule Viewer
```python
from capsule_viewer import CapsuleViewer

# Create capsule viewer
viewer = CapsuleViewer(parent_frame, "/path/to/vvault")

# Load capsule
viewer.load_capsule("/path/to/capsule.capsule")

# Analyze capsule
analysis = viewer.generate_capsule_analysis()
```

## 🤝 Contributing

### Development Setup
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests for new functionality
5. Submit a pull request

### Code Style
- Follow PEP 8 guidelines
- Use type hints for function parameters and return values
- Add docstrings for all public functions and classes
- Include comprehensive error handling

### Testing
- Run the test suite: `python3 -m pytest tests/`
- Test individual components: `python3 test_<component>.py`
- Integration testing: Use the desktop application

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🆘 Support

For support and questions:
- Check the troubleshooting section above
- Review the log files for error messages
- Create an issue in the repository
- Contact the development team

## 🔄 Updates

### Version 1.0.0
- Initial release with full desktop application
- Integrated GUI with all VVAULT components
- Blockchain synchronization capabilities
- Comprehensive security layer
- Advanced capsule viewer and analysis

### Planned Features
- Mobile application support
- Cloud synchronization
- Advanced analytics dashboard
- Machine learning integration
- API gateway for external access

---

**VVAULT Desktop Application** - Secure AI Construct Memory Management with Blockchain Integration
