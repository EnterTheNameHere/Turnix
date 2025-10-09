// eslint.config.js (flat config for ESLint v9+)
import js from '@eslint/js';
import globals from 'globals';

export default [
    // Global ignores
    {
        ignores: [
            '**/node_modules/**',
            '**/dist/**',
            '**/build/**',
            '**/.vite/**',
            '**/.cache/**'
        ],
    },

    // ---------- Browser / renderer code ----------
    {
        files: [
            '**/*.{js,mjs,cjs}',
        ],
        languageOptions: {
            ecmaVersion: 'latest',
            sourceType: 'module',
            globals: {
                ...globals.browser,
                ...globals.es2024,
            },
        },
        rules: {
            ...js.configs.recommended.rules,
            'no-unused-vars': ['warn', {argsIgnorePattern: '^_'}],
            'no-undef': ['error'],
            'semi': ['error', 'always'],
            'quotes': ['error', 'single'],
            'indent': ['error', 4, {SwitchCase: 1}],
            'no-invalid-this': ['error'],
            'default-param-last': ['error'],
            'no-param-reassign': ['error'],
        },
    },
];
