CREATE TABLE orders (
    id VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL,
    amount DECIMAL(12, 2) NOT NULL,
    status VARCHAR(20) NOT NULL
);

CREATE TABLE wallets (
    user_id VARCHAR(64) PRIMARY KEY,
    balance DECIMAL(12, 2) NOT NULL DEFAULT 0
);
