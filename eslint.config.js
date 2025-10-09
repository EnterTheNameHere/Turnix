// eslint.config.js (flat config for ESLint v9+)
import js from '@eslint/js';
import htmlPlugin from 'eslint-plugin-html';
import htmlParser from '@html-eslint/parser';
import htmlRules from '@html-eslint/eslint-plugin';
import importPlugin from 'eslint-plugin-import';
import promisePlugin from 'eslint-plugin-promise';
import globals from 'globals';

// ---------- Shared JavaScript config ----------
const jsCommon = {
    languageOptions: {
        ecmaVersion: 'latest',
        sourceType: 'module',
        globals: {
            ...globals.browser,
            ...globals.es2024,
            turnixImport: 'readonly',
        },
    },
    plugins: {
        import: importPlugin,
        promise: promisePlugin,
    },
    settings: {
        // Help import/no-unresolved resolve None/Electron-style imports
        'import/resolver': {
            node: { extensions: ['.js', '.mjs', '.cjs'] },
        },
        // Treat Electron as a core/builtin to avoid false positives
        'import/core-modules': ['electron'],
    },
    rules: {
        // Base JS
        ...js.configs.recommended.rules,

        // Import plugin
        ...(importPlugin.configs?.recommended?.rules ?? {}),

        // Promise plugin
        ...(promisePlugin.configs?.['flat/recommended']?.rules ?? {}),

        // Custom rules
        'no-unused-vars': ['warn', {argsIgnorePattern: '^_'}],
        'no-undef': ['error'],
        'semi': ['error', 'always'],
        'quotes': ['error', 'single'],
        'indent': ['error', 4, {SwitchCase: 1}],
        'no-invalid-this': ['error'],
        'default-param-last': ['error'],
        'no-param-reassign': ['error'],
        'import/no-unresolved': ['off'],
    },
};

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

    // ---------- JavaScript files ----------
    {
        files: [
            '**/*.{js,mjs,cjs}',
        ],
        ...jsCommon,
    },
    // ---------- JavaScript inside <script> in .html ----------
    {
        files: ['**/*.html'],
        plugins: { html: htmlPlugin, ...jsCommon.plugins },
        settings: jsCommon.settings,
        languageOptions: jsCommon.languageOptions,
        rules: jsCommon.rules,
    },
    // ---------- HTML markup itself ----------
    {
        files: ['**/*.html'],
        languageOptions: { parser: htmlParser },
        plugins: { '@html-eslint': htmlRules },
        rules: {
            '@html-eslint/indent': ['error', 4],
            '@html-eslint/no-duplicate-attrs': ['error'],
            '@html-eslint/no-multiple-empty-lines': ['error', { max: 1 }],
            '@html-eslint/no-trailing-spaces': ['error'],
            '@html-eslint/require-doctype': ['error'],
        },
    },
];
