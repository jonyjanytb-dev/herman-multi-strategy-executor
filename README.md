# Herman Multi-Strategy Executor

私有多策略自动交易执行器。当前把 HermanTrading 的两套不同交易逻辑统一到同一个本地终端、同一套 Hyperliquid / OKX 执行层中，同时保持**策略逻辑、运行状态和保护单状态彼此隔离**。

## 当前策略

### 1.0 · Trend Rebalance Map

底层逻辑：**均线拉伸后的再平衡 / Mean Reversion**。

- SMA50 / SMA200
- LONG：价格收盘重新上穿 SMA50，同时 `SMA50 < SMA200`
- SHORT：价格收盘重新跌破 SMA50，同时 `SMA50 > SMA200`
- SMA50 与 SMA200 分离距离默认必须 > 30 points
- 默认 TP：SMA200 / Dynamic
- 默认 SL：125 points Fixed
- 一次只管理一个仓位

来源：<https://github.com/HermanTrading/Trend-Rebalance-Map-Herman->

### 1.1 · Streak Failure Reversal

底层逻辑：**连续单边 K 线后的动能失败反转 / Exhaustion + Failure Confirmation**。

默认执行合同：

- 标准 1m 图表；Signal timeframe 可选 1m / 5m
- 默认连续 5 根 bullish / bearish candle bodies 形成 setup
- 连续上涨 streak 完成后只 `ARM SHORT`，不会立刻做空
- 后续最多等待 15 根 signal candle
- 只有后续已确认 K 线的 **Close < terminal streak candle Low** 才确认 SHORT
- LONG 完全镜像
- Wick 单独突破不触发
- Setup 触发后即消费，不重复使用
- 默认 Session：09:45–12:00 America/New_York
- 默认 16:00 ET hard flat
- 默认 SL：terminal streak candle extreme
- 默认 TP：1R，成交后 TP / SL 冻结，不追踪
- 真实执行在确认 K 收盘后立即发市价/主动单，对应 Pine 的“下一根可用 1m bar open”模型

来源：<https://github.com/HermanTrading/Streak-Failure-Reversal-Herman->

> 1.1 上游源码目前没有在仓库根目录提供单独 LICENSE，因此本私有仓库只保留来源链接和工程实现，不重新发布上游完整 Pine 源码。

## 两套策略的核心差异

| 项目 | 1.0 Trend Rebalance | 1.1 Streak Failure Reversal |
|---|---|---|
| 核心假设 | 偏离均值后会向慢均线回归 | 连续动能后出现结构失败会反转 |
| Setup | SMA50 / SMA200 拉开 | 连续多根同方向 K 线 |
| Trigger | Close 重新穿越 SMA50 | 后续 Close 破坏 terminal streak candle 极值 |
| 信号结构 | 单阶段 | Setup → Armed → Confirmation 两阶段 |
| 默认 TP | Dynamic SMA200 | Frozen 1R |
| 默认 SL | Fixed 125 points | 结构型 terminal streak extreme |
| Session | 无固定 Session | 默认纽约 09:45–12:00 |
| Hard flat | 无 | 默认 16:00 ET |
| 更偏向 | Mean Reversion | Exhaustion / Reversal |

## 哪种行情更适合

### 1.0 更适合

- 指数出现明显拉伸，但仍有较强“回归均值”特征
- SMA50 与 SMA200 已经拉开，价格开始重新穿回快均线
- 震荡偏趋势、冲高/杀跌后回拉的 intraday 环境

持续单边加速且几乎不回撤的趋势日通常对 1.0 更不友好，因为它的核心假设就是价格会重新向慢均线平衡。

### 1.1 更适合

- 开盘后出现连续同方向冲刺
- 市场短时间形成明显 streak / exhaustion
- 随后价格真正破坏最后一根 streak K 的结构，而不是只打出影线
- 美国指数日内、尤其纽约早盘的 failure reversal 场景

它比“连续 5 根就直接摸顶/抄底”更严格，因为必须等 failure confirmation。

## 终端策略切换

运行：

```bash
bash run_local.sh
```

终端提供：

```text
1) 启动机器人
2) 选择策略
3) 设置每笔仓位 / 杠杆
4) 设置交易所 / API 凭证
5) 切换 DRY RUN / DEMO / LIVE
6) 设置做多 / 做空方向
7) 设置当前策略参数
8) 查询资金 / 当前账户
0) 退出
```

选择策略：

```text
1) 1.0 Trend Rebalance Map
2) 1.1 Streak Failure Reversal
```

切换策略时会自动回到 `DRY_RUN=true`，并使用不同状态文件，例如：

```text
runtime/state-hyperliquid-trend_rebalance.json
runtime/state-hyperliquid-streak_failure.json
```

不会把 1.0 的 active position state / last entry / Dynamic TP 状态错误带入 1.1。

## 交易所

当前执行层支持：

- Hyperliquid HIP-3，默认 `xyz:XYZ100`
- OKX linear perpetual swap
- DRY RUN
- OKX DEMO
- LIVE

`.env` 永远不提交 GitHub。

## 运行

```bash
git clone https://github.com/jonyjanytb-dev/herman-multi-strategy-executor.git
cd herman-multi-strategy-executor
bash run_local.sh
```

首次运行会创建本地 `.env`，默认 DRY RUN。

## 设计原则

- 不把 1.0 与 1.1 的内部状态混用
- 不把 1.1 简化成“连续 N 根后直接反手”
- 1.1 必须等待 confirmed close failure
- 1.0 保留 Dynamic SMA200 TP
- 1.1 TP / SL 在实际成交后冻结
- 每个 closed bar 只处理一次
- 网络/API 失败不能重复提交同一根 K 的入场信号
- LIVE 入场后优先确保保护单存在

## 风险

本仓库用于个人研究与自动交易工程实验。LIVE 模式会发送真实订单。策略历史表现不代表未来结果；滑点、网络、API、交易所、保护单和强平风险均由使用者承担。
