# Herman Multi-Strategy Executor

把 HermanTrading / @RHerman 公开的 TradingView 策略工程化落地为可本地运行的 Python 自动交易执行器。

当前支持三套彼此独立的策略逻辑，并共用 Hyperliquid / OKX / Lighter 执行层、同一套本地交互终端和统一凭证配置。

> 策略思想与原始 Pine Script 来源于 HermanTrading。本项目负责交易所接入、自动执行、保护单、状态恢复与多策略工程化落地；与 HermanTrading 不存在官方隶属、代理或合作关系，除非双方另有明确公开说明。

## 当前策略

| 版本 | 策略 | 核心逻辑 | 默认 TP | 默认 SL |
|---|---|---|---|---|
| 1.0 | Trend Rebalance Map | SMA50 / SMA200 拉伸后的均值回归 | Dynamic SMA200 | Fixed 125 points |
| 1.1 | Streak Failure Reversal | 连续 K 线后的 exhaustion + failure confirmation | Frozen 1R | Terminal streak extreme |
| 1.2 | AW Liquidity Reversal | Liquidity sweep → structure shift → FVG entry | Opposite liquidity / 1R fallback | Swept liquidity extreme |

### 1.0 · Trend Rebalance Map

核心逻辑：**均线拉伸后的再平衡 / Mean Reversion**。

- SMA50 / SMA200
- LONG：价格收盘重新上穿 SMA50，同时 `SMA50 < SMA200`
- SHORT：价格收盘重新跌破 SMA50，同时 `SMA50 > SMA200`
- SMA50 与 SMA200 分离距离默认必须 > 30 points
- 默认 TP：SMA200 / Dynamic
- 默认 SL：125 points Fixed
- 一次只管理一个仓位

上游：<https://github.com/HermanTrading/Trend-Rebalance-Map-Herman->

### 1.1 · Streak Failure Reversal

核心逻辑：**连续单边 K 线后的动能失败反转 / Exhaustion + Failure Confirmation**。

默认执行合同：

- 标准 1m 图表；Signal timeframe 可选 1m / 5m
- 默认连续 5 根 bullish / bearish candle bodies 形成 setup
- 连续上涨 streak 完成后只 `ARM SHORT`，不会立即做空
- 后续最多等待 15 根 signal candle
- 只有后续确认 K 线的 **Close < terminal streak candle Low** 才确认 SHORT
- LONG 完全镜像
- Wick 单独突破不触发
- Setup 首次发生有效 break 后即消费，不重复使用
- 默认 Session：09:45–12:00 America/New_York
- 默认 16:00 ET hard flat
- 默认 SL：terminal streak candle extreme
- 默认 TP：1R
- 实际成交后 TP / SL 冻结，不做 Dynamic trailing

上游：<https://github.com/HermanTrading/Streak-Failure-Reversal-Herman->

> 1.1 上游仓库目前未提供单独 LICENSE。本仓库不重新发布其完整 Pine 源码，只保留来源链接，并提供独立的 Python 工程实现。

### 1.2 · AW Liquidity Reversal

核心逻辑：**流动性扫单后的结构反转 / Liquidity Reversal**。

- 通过已确认 swing high / swing low 建立未触及流动性池
- 默认额外纳入自动匹配的高周期 swing 流动性（1m→15m、5m→1h、15m→4h、30m→6h、1h→1d）
- 默认纳入 Previous Day High / Low
- LONG：扫前低并收回 → bullish displacement 收盘突破 neckline → 形成 bullish FVG → FVG 触发入场
- SHORT：完全镜像
- 默认 Swing Length：3
- 默认 ATR Length：2
- 默认 displacement：实体 >= 1.0 × ATR
- Sweep → Shift 最长等待 30 bars
- Shift → Entry 最长等待 10 bars
- 默认允许 Asia / London / NY AM，NY PM 关闭（America/New_York）
- 默认 SL：被 sweep 的结构极值
- 默认 TP：最近未触及的对侧流动性；无可用目标时回退 1R
- 实际成交后 TP / SL 冻结，不使用 Dynamic trailing

上游：<https://github.com/HermanTrading/aw_trades_-model>

> 上游仓库目前未提供单独 LICENSE，源文件头部也未声明独立软件许可证。因此本仓库不重新发布其完整 Pine 源码，只提供来源链接，并发布独立的 Python 工程实现。

> Hyperliquid / OKX 使用 1m 已收盘 K 线。Lighter 可选择 1m、5m、15m、30m 或 1h；AW 策略会按所选周期请求已收盘 K 线，并自动匹配更高周期流动性。首次启动会回看约 3000 根所选周期 K 线。

## 交易所与执行

当前支持：

- Hyperliquid HIP-3，默认 `xyz:XYZ100`
- OKX linear perpetual swap
- Lighter Core / Robinhood Chain Lighter perpetual，默认 `BTC`
- DRY RUN
- OKX DEMO
- LIVE
- 市价 / IOC 入场并设置最大允许滑点
- 原生 reduce-only TP / SL 保护单
- 每个策略独立 runtime state
- closed bar 去重，避免同一根 K 重复提交入场

1.0、1.1 和 1.2 共用同一个本地 `.env`，不需要切换策略时重新输入 API。策略切换会自动回到 DRY RUN，并使用独立状态文件，例如：

```text
runtime/state-hyperliquid-trend_rebalance.json
runtime/state-hyperliquid-streak_failure.json
runtime/state-hyperliquid-aw_liquidity.json
runtime/state-lighter-aw_liquidity-1m.json
runtime/state-lighter-aw_liquidity-5m.json
```

## 快速开始

```bash
git clone https://github.com/jonyjanytb-dev/herman-multi-strategy-executor.git
cd herman-multi-strategy-executor
bash run_local.sh
```

首次运行会创建本地 `.env`，默认 `DRY_RUN=true`。

终端：

```text
1) 启动机器人
2) 选择策略
3) 设置每笔仓位 / 杠杆
4) 设置交易所 / API 凭证
5) 切换 DRY RUN / DEMO / LIVE
6) 设置做多 / 做空方向
7) 设置当前策略参数
8) 设置 Lighter K 线周期
9) 查询资金 / 当前账户
0) 退出
```

选择策略：

```text
1) 1.0 Trend Rebalance Map
2) 1.1 Streak Failure Reversal
3) 1.2 AW Liquidity Reversal
```


## Lighter

Lighter 接口使用官方 `lighter-sdk` 进行签名交易，并用公开 REST API 获取市场数据和账户状态。

支持实例：

```text
mainnet            = Lighter Core
robinhood          = Robinhood Chain Lighter
testnet            = Lighter Core Testnet
robinhood_testnet  = Robinhood Chain Testnet
```

默认标的是 `BTC`。LIVE 模式需要：

```text
LIGHTER_ACCOUNT_INDEX
LIGHTER_API_KEY_INDEX
LIGHTER_API_KEY_PRIVATE
```

Lighter K 线周期可在终端菜单 `8` 中选择：

```text
1) 1m
2) 5m
3) 15m
4) 30m
5) 1h
0) 返回
```

切换周期会自动切回 `DRY RUN`、为 AW 策略匹配更高分析周期，并使用独立状态文件。确认模拟运行正常后，再手动切回 LIVE。策略 1.1 暂时固定使用 1m。

这里的 `LIGHTER_API_KEY_PRIVATE` 是 **Lighter API Key 私钥**，不是钱包 / ETH 主私钥。机器人运行交易时不需要把钱包主私钥放进项目。

策略信号触发后，Lighter 执行层使用 Market + IOC 语义，并通过 `MAX_SLIPPAGE` 限制可接受成交滑点。SL / TP 使用交易所原生 reduce-only trigger orders；重启后会尝试从 Lighter 当前活动订单中恢复保护单。

## Hyperliquid

如果你准备使用 Hyperliquid，可以通过下面的邀请链接注册：

<https://app.hyperliquid.xyz/join/JONY2019>

该链接包含推荐关系（referral）。是否使用邀请链接不影响本项目源码和功能。

## 安全

- `.env` 已加入 `.gitignore`，不要提交真实密钥
- Hyperliquid 使用单独授权的 API Wallet，不要使用主钱包私钥
- Lighter 只保存专用 API Key 私钥，不要把钱包 / ETH 主私钥写入 `.env`
- OKX API 建议仅开启 Read + Trade，不开启 Withdraw
- 更新代码后先用 DRY RUN / DEMO 验证
- LIVE 开仓后请人工确认交易所原生 SL / TP 已成功存在

更多说明见 `SECURITY.md`。

## Attribution / 第三方声明

详细策略来源、许可说明与免责声明见 `THIRD_PARTY_NOTICES.md`。

1.0 上游 Pine Script 明确以 Mozilla Public License 2.0 发布。本仓库采用 MPL-2.0 作为项目许可框架。

## 风险提示

本仓库用于个人研究与自动交易工程实验。LIVE 模式会发送真实订单。策略历史表现不代表未来结果；滑点、网络、API、交易所、保护单、杠杆和强平风险均由使用者自行承担。

本项目不承诺盈利，也不构成投资建议。
