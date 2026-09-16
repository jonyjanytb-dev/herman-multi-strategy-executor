# Security Policy / 安全说明

本项目会连接真实交易账户，请把凭证安全放在第一优先级。

## 不要公开

请不要在 Issue、Pull Request、日志、截图或聊天中提交：

- `.env`
- Hyperliquid API Wallet Private Key
- 主钱包私钥 / 助记词
- OKX API Secret / Passphrase
- 任何具备提现权限的 API 凭证

仓库已通过 `.gitignore` 排除 `.env`。如果凭证曾经进入 Git 历史，即使随后删除文件，也应立即撤销并轮换该凭证。

## 推荐权限

### Hyperliquid

使用单独授权的 API Wallet 进行交易，不要把主钱包私钥放入机器人。

### OKX

API Key 建议只授予 `Read + Trade`，不要授予 `Withdraw`；条件允许时绑定固定 IP。

## LIVE 风险

LIVE 模式会发送真实订单。首次部署或更新后，应优先使用 DRY RUN / OKX DEMO，并人工验证：

- 交易账户和市场是否正确
- 名义仓位与杠杆是否正确
- 开仓后 SL 是否成功存在
- TP 是否成功存在并符合当前策略逻辑
- 重启恢复逻辑是否能识别已有仓位和保护单

如果发现可能泄露凭证、错误扩大真实仓位、删除保护单或导致重复下单的问题，请不要在公开 Issue 中附带真实密钥或账户机密。
