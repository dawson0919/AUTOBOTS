// Simplified PineScript Parser
// Converts TradingView PineScript code into executable JavaScript strategy functions

const indicators = require('./indicators');

/**
 * Parse PineScript code and return a strategy function
 * Supports a subset of PineScript v5 syntax:
 * - strategy(), indicator()
 * - ta.sma(), ta.ema(), ta.rsi(), ta.crossover(), ta.crossunder()
 * - strategy.entry(), strategy.close()
 * - close, open, high, low, volume
 * - Basic comparisons and logic
 */
function parsePineScript(code) {
    // Extract strategy metadata
    const metadata = extractMetadata(code);

    // Parse the strategy logic into a JS function
    const strategyFn = compileStrategy(code);

    return {
        metadata,
        execute: strategyFn
    };
}

function extractMetadata(code) {
    const meta = {
        name: 'Custom Strategy',
        overlay: true,
        defaultQty: 1
    };

    // Match strategy() declaration
    const stratMatch = code.match(/strategy\s*\(\s*["']([^"']+)["']/);
    if (stratMatch) meta.name = stratMatch[1];

    // Match overlay
    const overlayMatch = code.match(/overlay\s*=\s*(true|false)/);
    if (overlayMatch) meta.overlay = overlayMatch[1] === 'true';

    return meta;
}

function compileStrategy(code) {
    // Normalize line endings
    const lines = code.replace(/\r\n/g, '\n').split('\n');

    // Track variable declarations and indicator computations
    const variables = {};
    const conditions = [];
    let buyCondition = null;
    let sellCondition = null;

    for (const rawLine of lines) {
        const line = rawLine.trim();
        if (!line || line.startsWith('//') || line.startsWith('strategy(') || line.startsWith('indicator(') || line.startsWith('//@')) continue;

        // Parse ta.sma() assignments
        const smaMatch = line.match(/(\w+)\s*=\s*ta\.sma\(\s*(close|open|high|low)\s*,\s*(\d+)\s*\)/);
        if (smaMatch) {
            variables[smaMatch[1]] = { type: 'sma', source: smaMatch[2], period: parseInt(smaMatch[3]) };
            continue;
        }

        // Parse ta.ema() assignments
        const emaMatch = line.match(/(\w+)\s*=\s*ta\.ema\(\s*(close|open|high|low)\s*,\s*(\d+)\s*\)/);
        if (emaMatch) {
            variables[emaMatch[1]] = { type: 'ema', source: emaMatch[2], period: parseInt(emaMatch[3]) };
            continue;
        }

        // Parse ta.rsi() assignments
        const rsiMatch = line.match(/(\w+)\s*=\s*ta\.rsi\(\s*(close|open|high|low)\s*,\s*(\d+)\s*\)/);
        if (rsiMatch) {
            variables[rsiMatch[1]] = { type: 'rsi', source: rsiMatch[2], period: parseInt(rsiMatch[3]) };
            continue;
        }

        // Parse ta.crossover() conditions
        const crossoverMatch = line.match(/(\w+)\s*=\s*ta\.crossover\(\s*(\w+)\s*,\s*(\w+)\s*\)/);
        if (crossoverMatch) {
            variables[crossoverMatch[1]] = { type: 'crossover', a: crossoverMatch[2], b: crossoverMatch[3] };
            continue;
        }

        // Parse ta.crossunder() conditions
        const crossunderMatch = line.match(/(\w+)\s*=\s*ta\.crossunder\(\s*(\w+)\s*,\s*(\w+)\s*\)/);
        if (crossunderMatch) {
            variables[crossunderMatch[1]] = { type: 'crossunder', a: crossunderMatch[2], b: crossunderMatch[3] };
            continue;
        }

        // Parse simple comparison conditions: varName = source > source2 or source < source2
        const compMatch = line.match(/(\w+)\s*=\s*(\w+)\s*(>|<|>=|<=|==)\s*(\w+)/);
        if (compMatch && !compMatch[0].includes('ta.') && !compMatch[0].includes('strategy.')) {
            variables[compMatch[1]] = { type: 'comparison', a: compMatch[2], op: compMatch[3], b: compMatch[4] };
            continue;
        }

        // Parse strategy.entry() - buy signal
        const entryMatch = line.match(/strategy\.entry\(\s*["']([^"']+)["']\s*,\s*strategy\.(long|short)\s*(?:,\s*when\s*=\s*(\w+))?\s*\)/i);
        if (entryMatch) {
            // Find the condition - could be in an if statement
            const ifMatch = rawLine.match(/if\s+(\w+)/);
            const condVar = ifMatch ? ifMatch[1] : entryMatch[3];
            if (entryMatch[2].toLowerCase() === 'long') {
                buyCondition = condVar || null;
            }
            continue;
        }

        // Parse if block with strategy.entry
        const ifEntryMatch = line.match(/^if\s+(.+)/);
        if (ifEntryMatch) {
            // Check next lines for strategy.entry or strategy.close
            conditions.push({ condition: ifEntryMatch[1].trim() });
            continue;
        }

        // Simpler strategy.entry without if
        const simpleEntryMatch = line.match(/strategy\.entry\(\s*["']([^"']+)["']\s*,\s*strategy\.(long|short)\s*\)/i);
        if (simpleEntryMatch) {
            if (conditions.length > 0) {
                const lastCond = conditions[conditions.length - 1];
                if (simpleEntryMatch[2].toLowerCase() === 'long') {
                    buyCondition = lastCond.condition;
                }
            }
            continue;
        }

        // Parse strategy.close()
        const closeMatch = line.match(/strategy\.close\(\s*["']([^"']+)["']\s*\)/i);
        if (closeMatch) {
            if (conditions.length > 0) {
                sellCondition = conditions[conditions.length - 1].condition;
            }
            continue;
        }
    }

    // Build the strategy function
    return function (candles, indicatorData, i, indLib) {
        // Resolve variables
        const resolved = {};

        for (const [name, def] of Object.entries(variables)) {
            switch (def.type) {
                case 'sma':
                    if (!indicatorData.sma[def.period]) {
                        indicatorData.sma[def.period] = indLib.sma(indicatorData[def.source] || indicatorData.close, def.period);
                    }
                    resolved[name] = indicatorData.sma[def.period];
                    break;
                case 'ema':
                    if (!indicatorData.ema[def.period]) {
                        indicatorData.ema[def.period] = indLib.ema(indicatorData[def.source] || indicatorData.close, def.period);
                    }
                    resolved[name] = indicatorData.ema[def.period];
                    break;
                case 'rsi':
                    if (!indicatorData.rsi[def.period]) {
                        indicatorData.rsi[def.period] = indLib.rsi(indicatorData[def.source] || indicatorData.close, def.period);
                    }
                    resolved[name] = indicatorData.rsi[def.period];
                    break;
                case 'crossover': {
                    const aArr = getArray(def.a, resolved, indicatorData);
                    const bArr = getArray(def.b, resolved, indicatorData);
                    resolved[name] = indLib.crossover(aArr, bArr, i);
                    break;
                }
                case 'crossunder': {
                    const aArr = getArray(def.a, resolved, indicatorData);
                    const bArr = getArray(def.b, resolved, indicatorData);
                    resolved[name] = indLib.crossunder(aArr, bArr, i);
                    break;
                }
                case 'comparison': {
                    const aVal = getValue(def.a, resolved, indicatorData, i);
                    const bVal = getValue(def.b, resolved, indicatorData, i);
                    switch (def.op) {
                        case '>': resolved[name] = aVal > bVal; break;
                        case '<': resolved[name] = aVal < bVal; break;
                        case '>=': resolved[name] = aVal >= bVal; break;
                        case '<=': resolved[name] = aVal <= bVal; break;
                        case '==': resolved[name] = aVal == bVal; break;
                    }
                    break;
                }
            }
        }

        // Evaluate buy/sell conditions
        if (buyCondition && evaluateCondition(buyCondition, resolved, indicatorData, i)) {
            return 'BUY';
        }
        if (sellCondition && evaluateCondition(sellCondition, resolved, indicatorData, i)) {
            return 'SELL';
        }

        return null;
    };
}

// Helper: get an array value (could be a variable or a price series)
function getArray(name, resolved, indicatorData) {
    if (resolved[name] && Array.isArray(resolved[name])) return resolved[name];
    if (indicatorData[name]) return indicatorData[name];
    // Try to parse as price series
    const priceMap = { close: 'close', open: 'open', high: 'high', low: 'low', volume: 'volume' };
    if (priceMap[name]) return indicatorData[priceMap[name]];
    return [];
}

// Helper: get a single value at index i
function getValue(name, resolved, indicatorData, i) {
    // Check if it's a number
    if (!isNaN(name)) return parseFloat(name);

    // Check resolved variables
    if (typeof resolved[name] === 'boolean' || typeof resolved[name] === 'number') return resolved[name];
    if (Array.isArray(resolved[name])) return resolved[name][i];

    // Check indicator data
    if (Array.isArray(indicatorData[name])) return indicatorData[name][i];

    // Price series
    const priceMap = { close: 'close', open: 'open', high: 'high', low: 'low', volume: 'volume' };
    if (priceMap[name] && Array.isArray(indicatorData[priceMap[name]])) {
        return indicatorData[priceMap[name]][i];
    }

    return null;
}

// Helper: evaluate a condition string
function evaluateCondition(condition, resolved, indicatorData, i) {
    // Direct boolean variable
    if (typeof resolved[condition] === 'boolean') return resolved[condition];

    // Simple variable check
    if (resolved[condition] !== undefined) return !!resolved[condition];

    // Try to parse as comparison
    const compMatch = condition.match(/(\w+)\s*(>|<|>=|<=|==|!=)\s*(\w+)/);
    if (compMatch) {
        const a = getValue(compMatch[1], resolved, indicatorData, i);
        const b = getValue(compMatch[3], resolved, indicatorData, i);
        switch (compMatch[2]) {
            case '>': return a > b;
            case '<': return a < b;
            case '>=': return a >= b;
            case '<=': return a <= b;
            case '==': return a == b;
            case '!=': return a != b;
        }
    }

    return false;
}

module.exports = {
    parsePineScript
};
