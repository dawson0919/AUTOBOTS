const { createClient } = require('@supabase/supabase-js');
require('dotenv').config();

const supabaseUrl = process.env.SUPABASE_URL;
const supabaseKey = process.env.SUPABASE_SERVICE_ROLE_KEY;

if (!supabaseUrl || !supabaseKey) {
    console.error('Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY');
    process.exit(1);
}

const supabase = createClient(supabaseUrl, supabaseKey);

async function runMigration() {
    console.log('Starting migration: Adding latest_signal column to subscriptions table...');

    // We use a raw SQL query via rpc if possible, or just standard query if permissions allow.
    // Since we are using service_role, we might be able to use the REST API to update schema if enabled,
    // but usually schema changes require SQL Editor.
    // However, I will try to use a function or just hope the user runs the SQL manually if this fails.

    // Actually, the most reliable way without SQL editor access is to use the 'postgres' library if available,
    // but here we only have supabase-js.
    // Let's try to check if the column exists first by selecting one row.

    const { data, error } = await supabase.from('subscriptions').select('latest_signal').limit(1);

    if (error) {
        console.log('Column likely missing. Please run this SQL in your Supabase SQL Editor:');
        console.log(`
        ALTER TABLE subscriptions 
        ADD COLUMN IF NOT EXISTS latest_signal JSONB;
        
        ALTER TABLE subscriptions 
        ADD COLUMN IF NOT EXISTS last_updated TIMESTAMP WITH TIME ZONE DEFAULT NOW();
        `);

        // Since I cannot execute DDL via supabase-js client directly (unless rpc is set up),
        // I will Ask the User to do it or try to use a tool if available.
        // Wait, I can try to use the 'rpc' method if there is a wrapper, but standard setups dont have it.

        console.log('For User Agent: I will try to use the MCP tool again or ask user.');
    } else {
        console.log('Column latest_signal already exists!');
    }
}

runMigration();
