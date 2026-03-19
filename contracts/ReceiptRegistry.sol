// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/**
 * @title ReceiptRegistry
 * @notice On-chain registry for REE (Reproducible Execution Environment) receipts.
 *         Each receipt is anchored with its IPFS CID so anyone can fetch
 *         the full JSON and verify the computation independently.
 */
contract ReceiptRegistry {

    struct ReceiptRecord {
        string  receiptHash;   // cryptographic hash from REE
        string  ipfsCid;       // IPFS CID of the full receipt JSON
        string  modelName;     // e.g. "Qwen/Qwen2-0.5B"
        address submitter;     // wallet that submitted
        uint256 timestamp;     // block timestamp
    }

    // id => record
    mapping(bytes32 => ReceiptRecord) public receipts;
    bytes32[] public allIds;

    event ReceiptSubmitted(
        bytes32 indexed id,
        string  receiptHash,
        string  ipfsCid,
        string  modelName,
        address indexed submitter,
        uint256 timestamp
    );

    /**
     * @notice Submit a new REE receipt.
     * @param receiptHash  The receipt_hash field from the REE receipt JSON.
     * @param ipfsCid      IPFS CID where the full receipt JSON is pinned.
     * @param modelName    HuggingFace model ID used for inference.
     * @return id          Unique on-chain identifier for this record.
     */
    function submitReceipt(
        string calldata receiptHash,
        string calldata ipfsCid,
        string calldata modelName
    ) external returns (bytes32 id) {
        id = keccak256(abi.encodePacked(receiptHash, msg.sender, block.timestamp));

        receipts[id] = ReceiptRecord({
            receiptHash: receiptHash,
            ipfsCid:     ipfsCid,
            modelName:   modelName,
            submitter:   msg.sender,
            timestamp:   block.timestamp
        });

        allIds.push(id);

        emit ReceiptSubmitted(id, receiptHash, ipfsCid, modelName, msg.sender, block.timestamp);
    }

    /// @notice Fetch a record by its on-chain id.
    function getReceipt(bytes32 id) external view returns (ReceiptRecord memory) {
        return receipts[id];
    }

    /// @notice Total number of receipts registered.
    function totalReceipts() external view returns (uint256) {
        return allIds.length;
    }
}
