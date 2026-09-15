from enum import StrEnum


class TrafficContractKeyType(StrEnum):
    RPM = "rpm"
    TPM = "tpm"


class TrafficContractMode(StrEnum):
    NORMAL = "normal"
    BORROWED = "borrowed"
    DEGRADED = "degraded"
