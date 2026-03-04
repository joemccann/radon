#!/bin/bash
# Patches for pi-coding-agent framework bugs
#
# Bug 1: compaction.js - TypeError: message.content is not iterable
#   estimateTokens doesn't handle null/undefined content in toolResult messages
#
# Bug 2: anthropic.js - Cannot read properties of undefined (reading 'some')
#   convertContentBlocks crashes when toolResult has undefined content
#
# Bug 3: assistant-message.js - Cannot read properties of undefined (reading 'some')
#   updateContent crashes when message.content is undefined

set -e

PI_BASE=$(npm root -g)/@mariozechner/pi-coding-agent/dist
PATCHED=0
FAILED=0

# ============================================================================
# Patch 1: compaction.js - guard non-iterable content
# ============================================================================
COMPACTION_PATH="$PI_BASE/core/compaction/compaction.js"

if [ -f "$COMPACTION_PATH" ]; then
    if grep -q "Array.isArray(message.content)" "$COMPACTION_PATH"; then
        echo "✅ compaction.js: already patched"
    else
        cp "$COMPACTION_PATH" "${COMPACTION_PATH}.bak"
        sed -i.tmp '
/case "custom":/,/return Math\.ceil(chars \/ 4);/ {
    s/else {/else if (message.content \&\& Array.isArray(message.content)) {/
}
' "$COMPACTION_PATH"
        rm -f "${COMPACTION_PATH}.tmp"
        if grep -q "Array.isArray(message.content)" "$COMPACTION_PATH"; then
            echo "✅ compaction.js: patched"
            PATCHED=$((PATCHED + 1))
        else
            echo "❌ compaction.js: patch failed, restoring"
            cp "${COMPACTION_PATH}.bak" "$COMPACTION_PATH"
            FAILED=$((FAILED + 1))
        fi
    fi
else
    echo "⚠️  compaction.js: not found"
fi

# ============================================================================
# Patch 2: anthropic.js - guard convertContentBlocks against undefined content
# ============================================================================
ANTHROPIC_PATH="$PI_BASE/../node_modules/@mariozechner/pi-ai/dist/providers/anthropic.js"

if [ ! -f "$ANTHROPIC_PATH" ]; then
    # Try alternative path (hoisted)
    ANTHROPIC_PATH=$(find "$(npm root -g)/@mariozechner" -path "*/pi-ai/dist/providers/anthropic.js" 2>/dev/null | head -1)
fi

if [ -n "$ANTHROPIC_PATH" ] && [ -f "$ANTHROPIC_PATH" ]; then
    if grep -q "!content || !Array.isArray(content)" "$ANTHROPIC_PATH"; then
        echo "✅ anthropic.js: already patched"
    else
        cp "$ANTHROPIC_PATH" "${ANTHROPIC_PATH}.bak"
        # Replace the convertContentBlocks function's first line to add a guard
        sed -i.tmp 's/function convertContentBlocks(content) {/function convertContentBlocks(content) {\n    if (!content || !Array.isArray(content)) {\n        return "No content provided";\n    }/' "$ANTHROPIC_PATH"
        rm -f "${ANTHROPIC_PATH}.tmp"
        if grep -q "!content || !Array.isArray(content)" "$ANTHROPIC_PATH"; then
            echo "✅ anthropic.js: patched"
            PATCHED=$((PATCHED + 1))
        else
            echo "❌ anthropic.js: patch failed, restoring"
            cp "${ANTHROPIC_PATH}.bak" "$ANTHROPIC_PATH"
            FAILED=$((FAILED + 1))
        fi
    fi
else
    echo "⚠️  anthropic.js: not found"
fi

# ============================================================================
# Patch 3: assistant-message.js - guard message.content.some()
# ============================================================================
ASSISTANT_MSG_PATH="$PI_BASE/modes/interactive/components/assistant-message.js"

if [ -f "$ASSISTANT_MSG_PATH" ]; then
    if grep -q "message.content && message.content.some" "$ASSISTANT_MSG_PATH"; then
        echo "✅ assistant-message.js: already patched"
    else
        cp "$ASSISTANT_MSG_PATH" "${ASSISTANT_MSG_PATH}.bak"
        # Guard all three message.content.some() calls
        sed -i.tmp 's/message\.content\.some(/message.content \&\& message.content.some(/g' "$ASSISTANT_MSG_PATH"
        # Guard message.content.length
        sed -i.tmp 's/message\.content\.length/message.content \&\& message.content.length/g' "$ASSISTANT_MSG_PATH"
        rm -f "${ASSISTANT_MSG_PATH}.tmp"
        if grep -q "message.content && message.content.some" "$ASSISTANT_MSG_PATH"; then
            echo "✅ assistant-message.js: patched"
            PATCHED=$((PATCHED + 1))
        else
            echo "❌ assistant-message.js: patch failed, restoring"
            cp "${ASSISTANT_MSG_PATH}.bak" "$ASSISTANT_MSG_PATH"
            FAILED=$((FAILED + 1))
        fi
    fi
else
    echo "⚠️  assistant-message.js: not found"
fi

echo ""
echo "── Summary: $PATCHED patched, $FAILED failed ──"
[ "$FAILED" -eq 0 ] || exit 1
