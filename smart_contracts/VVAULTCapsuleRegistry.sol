// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/**
 * @title VVAULT Capsule Registry
 * @dev Smart contract for registering and verifying VVAULT AI construct capsules
 * @author Devon Allen Woodson
 * @notice This contract provides immutable storage and verification of AI construct capsules
 */

contract VVAULTCapsuleRegistry {
    // Events
    event CapsuleRegistered(
        string indexed fingerprint,
        string indexed instanceName,
        string capsuleId,
        string ipfsHash,
        address indexed owner,
        uint256 timestamp
    );
    
    event CapsuleVerified(
        string indexed fingerprint,
        bool isValid,
        uint256 timestamp
    );
    
    event CapsuleUpdated(
        string indexed fingerprint,
        string newIpfsHash,
        address indexed owner,
        uint256 timestamp
    );
    
    // Structs
    struct CapsuleRecord {
        string fingerprint;
        string instanceName;
        string capsuleId;
        string ipfsHash;
        address owner;
        uint256 timestamp;
        uint256 blockNumber;
        bool isActive;
        uint256 updateCount;
    }
    
    struct VerificationResult {
        bool exists;
        bool isActive;
        bool fingerprintMatch;
        bool ownerMatch;
        uint256 timestamp;
    }
    
    // State variables
    mapping(string => CapsuleRecord) public capsules;
    mapping(address => string[]) public ownerCapsules;
    mapping(string => bool) public registeredFingerprints;
    
    string[] public allFingerprints;
    uint256 public totalCapsules;
    address public owner;
    
    // Modifiers
    modifier onlyOwner() {
        require(msg.sender == owner, "Only owner can perform this action");
        _;
    }
    
    modifier capsuleExists(string memory fingerprint) {
        require(registeredFingerprints[fingerprint], "Capsule does not exist");
        _;
    }
    
    modifier capsuleNotExists(string memory fingerprint) {
        require(!registeredFingerprints[fingerprint], "Capsule already exists");
        _;
    }
    
    // Constructor
    constructor() {
        owner = msg.sender;
    }
    
    /**
     * @dev Register a new capsule
     * @param fingerprint SHA-256 hash of the capsule
     * @param instanceName Name of the AI construct instance
     * @param capsuleId Unique identifier for the capsule
     * @param ipfsHash IPFS hash where capsule data is stored
     */
    function registerCapsule(
        string memory fingerprint,
        string memory instanceName,
        string memory capsuleId,
        string memory ipfsHash
    ) external capsuleNotExists(fingerprint) {
        require(bytes(fingerprint).length > 0, "Fingerprint cannot be empty");
        require(bytes(instanceName).length > 0, "Instance name cannot be empty");
        require(bytes(capsuleId).length > 0, "Capsule ID cannot be empty");
        
        // Create capsule record
        CapsuleRecord memory newCapsule = CapsuleRecord({
            fingerprint: fingerprint,
            instanceName: instanceName,
            capsuleId: capsuleId,
            ipfsHash: ipfsHash,
            owner: msg.sender,
            timestamp: block.timestamp,
            blockNumber: block.number,
            isActive: true,
            updateCount: 0
        });
        
        // Store capsule
        capsules[fingerprint] = newCapsule;
        registeredFingerprints[fingerprint] = true;
        allFingerprints.push(fingerprint);
        ownerCapsules[msg.sender].push(fingerprint);
        totalCapsules++;
        
        // Emit event
        emit CapsuleRegistered(
            fingerprint,
            instanceName,
            capsuleId,
            ipfsHash,
            msg.sender,
            block.timestamp
        );
    }
    
    /**
     * @dev Get capsule information
     * @param fingerprint SHA-256 hash of the capsule
     * @return CapsuleRecord with all capsule information
     */
    function getCapsule(string memory fingerprint) 
        external 
        view 
        capsuleExists(fingerprint) 
        returns (CapsuleRecord memory) 
    {
        return capsules[fingerprint];
    }
    
    /**
     * @dev Verify capsule integrity
     * @param fingerprint SHA-256 hash of the capsule
     * @return VerificationResult with verification status
     */
    function verifyCapsule(string memory fingerprint) 
        external 
        view 
        returns (VerificationResult memory) 
    {
        bool exists = registeredFingerprints[fingerprint];
        bool isActive = exists && capsules[fingerprint].isActive;
        bool fingerprintMatch = exists && 
            keccak256(bytes(capsules[fingerprint].fingerprint)) == keccak256(bytes(fingerprint));
        bool ownerMatch = exists && capsules[fingerprint].owner == msg.sender;
        
        return VerificationResult({
            exists: exists,
            isActive: isActive,
            fingerprintMatch: fingerprintMatch,
            ownerMatch: ownerMatch,
            timestamp: exists ? capsules[fingerprint].timestamp : 0
        });
    }
    
    /**
     * @dev Update capsule IPFS hash (only by owner)
     * @param fingerprint SHA-256 hash of the capsule
     * @param newIpfsHash New IPFS hash
     */
    function updateCapsuleIpfsHash(
        string memory fingerprint, 
        string memory newIpfsHash
    ) external capsuleExists(fingerprint) {
        require(capsules[fingerprint].owner == msg.sender, "Only capsule owner can update");
        require(bytes(newIpfsHash).length > 0, "IPFS hash cannot be empty");
        
        capsules[fingerprint].ipfsHash = newIpfsHash;
        capsules[fingerprint].updateCount++;
        
        emit CapsuleUpdated(fingerprint, newIpfsHash, msg.sender, block.timestamp);
    }
    
    /**
     * @dev Deactivate capsule (only by owner)
     * @param fingerprint SHA-256 hash of the capsule
     */
    function deactivateCapsule(string memory fingerprint) 
        external 
        capsuleExists(fingerprint) 
    {
        require(capsules[fingerprint].owner == msg.sender, "Only capsule owner can deactivate");
        
        capsules[fingerprint].isActive = false;
        
        emit CapsuleVerified(fingerprint, false, block.timestamp);
    }
    
    /**
     * @dev Get all capsules for an owner
     * @param ownerAddress Address of the owner
     * @return Array of capsule fingerprints
     */
    function getOwnerCapsules(address ownerAddress) 
        external 
        view 
        returns (string[] memory) 
    {
        return ownerCapsules[ownerAddress];
    }
    
    /**
     * @dev Get total number of capsules
     * @return Total number of registered capsules
     */
    function getTotalCapsules() external view returns (uint256) {
        return totalCapsules;
    }
    
    /**
     * @dev Get all registered fingerprints
     * @return Array of all capsule fingerprints
     */
    function getAllFingerprints() external view returns (string[] memory) {
        return allFingerprints;
    }
    
    /**
     * @dev Check if capsule exists
     * @param fingerprint SHA-256 hash of the capsule
     * @return True if capsule exists
     */
    function capsuleExists(string memory fingerprint) external view returns (bool) {
        return registeredFingerprints[fingerprint];
    }
    
    /**
     * @dev Get capsule count by instance
     * @param instanceName Name of the AI construct instance
     * @return Number of capsules for the instance
     */
    function getCapsuleCountByInstance(string memory instanceName) 
        external 
        view 
        returns (uint256) 
    {
        uint256 count = 0;
        for (uint256 i = 0; i < allFingerprints.length; i++) {
            if (keccak256(bytes(capsules[allFingerprints[i]].instanceName)) == 
                keccak256(bytes(instanceName))) {
                count++;
            }
        }
        return count;
    }
    
    /**
     * @dev Get capsules by instance name
     * @param instanceName Name of the AI construct instance
     * @return Array of capsule fingerprints for the instance
     */
    function getCapsulesByInstance(string memory instanceName) 
        external 
        view 
        returns (string[] memory) 
    {
        string[] memory instanceCapsules = new string[](totalCapsules);
        uint256 count = 0;
        
        for (uint256 i = 0; i < allFingerprints.length; i++) {
            if (keccak256(bytes(capsules[allFingerprints[i]].instanceName)) == 
                keccak256(bytes(instanceName))) {
                instanceCapsules[count] = allFingerprints[i];
                count++;
            }
        }
        
        // Resize array to actual count
        string[] memory result = new string[](count);
        for (uint256 i = 0; i < count; i++) {
            result[i] = instanceCapsules[i];
        }
        
        return result;
    }
    
    /**
     * @dev Emergency function to transfer ownership
     * @param newOwner Address of the new owner
     */
    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "New owner cannot be zero address");
        owner = newOwner;
    }
    
    /**
     * @dev Get contract statistics
     * @return Total capsules, active capsules, and contract owner
     */
    function getContractStats() external view returns (
        uint256 totalCapsulesCount,
        uint256 activeCapsulesCount,
        address contractOwner
    ) {
        totalCapsulesCount = totalCapsules;
        contractOwner = owner;
        
        // Count active capsules
        for (uint256 i = 0; i < allFingerprints.length; i++) {
            if (capsules[allFingerprints[i]].isActive) {
                activeCapsulesCount++;
            }
        }
    }
}








