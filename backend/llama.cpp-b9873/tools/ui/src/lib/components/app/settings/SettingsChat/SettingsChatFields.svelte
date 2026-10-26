<script lang="ts">
	import { RotateCcw, FlaskConical, Palette, Globe, AlertTriangle } from '@lucide/svelte';
	import { onMount } from 'svelte';
	import { i18n, t, SUPPORTED_LOCALES } from '$lib/stores/i18n.svelte';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Input } from '$lib/components/ui/input';
	import Label from '$lib/components/ui/label/label.svelte';
	import * as Select from '$lib/components/ui/select';
	import { Textarea } from '$lib/components/ui/textarea';
	import { SETTING_CONFIG_INFO, SETTINGS_KEYS } from '$lib/constants';
	import { SettingsFieldType } from '$lib/enums/settings.enums';
	import { settingsStore } from '$lib/stores/settings.svelte';
	import { serverStore } from '$lib/stores/server.svelte';
	import { modelsStore, selectedModelName, propsCacheVersion } from '$lib/stores/models.svelte';
	import { normalizeFloatingPoint } from '$lib/utils/precision';
	import { SettingsChatParameterSourceIndicator } from '$lib/components/app/settings';
	import DialogConfirmation from '$lib/components/app/dialogs/DialogConfirmation.svelte';
	import type { Component } from 'svelte';

	interface Props {
		fields: SettingsFieldConfig[];
		localConfig: SettingsConfigType;
		onConfigChange: (key: string, value: string | boolean) => void;
		onThemeChange?: (theme: string) => void;
	}

	let { fields, localConfig, onConfigChange, onThemeChange }: Props = $props();

	let currentModelParams = $derived.by(() => {
		propsCacheVersion();

		if (serverStore.isRouterMode) {
			const currentModelName = selectedModelName();

			if (currentModelName) {
				const currentModelProps = modelsStore.getModelProps(currentModelName);

				return (currentModelProps?.default_generation_settings?.params ?? {}) as Record<
					string,
					unknown
				>;
			}
		}
		return (serverStore.defaultParams ?? {}) as Record<string, unknown>;
	});

	let themeHue = $state(300);

	onMount(() => {
		const savedHue = localStorage.getItem('deining_theme_hue');
		if (savedHue !== null) {
			themeHue = parseInt(savedHue, 10);
		}
	});

	$effect(() => {
		localStorage.setItem('deining_theme_hue', themeHue.toString());
		if (typeof document !== 'undefined') {
			document.documentElement.style.setProperty('--theme-hue', themeHue.toString());
		}
	});

	function handleResetHue() {
		themeHue = 300;
	}

	let showDisableBaseRulesDialog = $state(false);

	function handleBaseRulesCheckChange(checked: boolean | 'indeterminate') {
		if (checked === false) {
			showDisableBaseRulesDialog = true;
		} else {
			onConfigChange(SETTINGS_KEYS.APPLY_BASE_OUTPUT_RULES, true);
		}
	}

	function handleConfirmDisableBaseRules() {
		onConfigChange(SETTINGS_KEYS.APPLY_BASE_OUTPUT_RULES, false);
		showDisableBaseRulesDialog = false;
	}

	function handleCancelDisableBaseRules() {
		showDisableBaseRulesDialog = false;
	}
</script>

{#each fields as field (field.key)}
	<div class="space-y-2">
		{#if field.type === SettingsFieldType.INPUT}
			{@const currentValue = String(localConfig[field.key] ?? '')}
			{@const serverDefault = currentModelParams[field.key]}
			{@const isCustomRealTime = (() => {
				if (serverDefault == null) return false;
				if (currentValue === '') return false;

				const numericInput = parseFloat(currentValue);
				const normalizedInput = !isNaN(numericInput)
					? Math.round(numericInput * 1000000) / 1000000
					: currentValue;
				const normalizedDefault =
					typeof serverDefault === 'number'
						? Math.round(serverDefault * 1000000) / 1000000
						: serverDefault;

				return normalizedInput !== normalizedDefault;
			})()}

			<div class="flex items-center gap-2">
				<Label for={field.key} class="flex items-center gap-1.5 text-sm font-medium">
					{field.label}

					{#if field.isExperimental}
						<FlaskConical class="h-3.5 w-3.5 text-muted-foreground" />
					{/if}
				</Label>
				{#if isCustomRealTime}
					<SettingsChatParameterSourceIndicator />
				{/if}
			</div>

			<div class="relative w-full">
				<Input
					id={field.key}
					type={field.isPositiveInteger ? 'number' : 'text'}
					{...field.isPositiveInteger ? { min: '1', step: '1' } : {}}
					value={currentValue}
					oninput={(e) => {
						// Update local config immediately for real-time badge feedback
						onConfigChange(field.key, e.currentTarget.value);
					}}
					placeholder={currentModelParams[field.key] != null
						? `Default: ${normalizeFloatingPoint(currentModelParams[field.key])}`
						: ''}
					class="w-full {isCustomRealTime ? 'pr-8' : ''}"
				/>
				{#if isCustomRealTime}
					<button
						type="button"
						onclick={() => {
							settingsStore.resetParameterToServerDefault(field.key);
							onConfigChange(field.key, '');
						}}
						class="absolute top-1/2 right-2 inline-flex h-5 w-5 -translate-y-1/2 items-center justify-center rounded transition-colors hover:bg-muted"
						aria-label="Reset to default"
						title="Reset to default"
					>
						<RotateCcw class="h-3 w-3" />
					</button>
				{/if}
			</div>
			{#if field.help || SETTING_CONFIG_INFO[field.key]}
				<p class="mt-1 text-xs text-muted-foreground">
					{@html field.help || SETTING_CONFIG_INFO[field.key]}
				</p>
			{/if}
		{:else if field.type === SettingsFieldType.TEXTAREA}
			{#if field.label}
				<Label for={field.key} class="block flex items-center gap-1.5 text-sm font-medium">
					{field.label}

					{#if field.isExperimental}
						<FlaskConical class="h-3.5 w-3.5 text-muted-foreground" />
					{/if}
				</Label>
			{/if}

			<Textarea
				id={field.key}
				value={String(localConfig[field.key] ?? '')}
				onchange={(e) => onConfigChange(field.key, e.currentTarget.value)}
				placeholder=""
				class="min-h-[10rem] w-full md:max-w-3xl"
			/>

			{#if field.help || SETTING_CONFIG_INFO[field.key]}
				<p class="mt-1 text-xs text-muted-foreground">
					{field.help || SETTING_CONFIG_INFO[field.key]}
				</p>
			{/if}

			{#if field.key === SETTINGS_KEYS.SYSTEM_MESSAGE}
				<div class="mt-3 space-y-3">
					<div class="flex items-start gap-2">
						<Checkbox
							id="applyBaseOutputRules"
							checked={Boolean(localConfig.applyBaseOutputRules ?? true)}
							onCheckedChange={handleBaseRulesCheckChange}
							class="mt-0.5"
						/>
						<div class="space-y-0.5">
							<Label for="applyBaseOutputRules" class="cursor-pointer text-sm font-medium">
								出力基盤ルール（ト書き記法）を適用する
							</Label>
							<p class="text-xs text-muted-foreground">
								キャラクターの動作・情景描写（*...*）と発話セリフを分離します。Live2D表情連動およびTTS音声読み上げに必要です。
							</p>
						</div>
					</div>

					<div class="flex items-center gap-2">
						<Checkbox
							id="showSystemMessage"
							checked={Boolean(localConfig.showSystemMessage ?? true)}
							onCheckedChange={(checked) =>
								onConfigChange(SETTINGS_KEYS.SHOW_SYSTEM_MESSAGE, Boolean(checked))}
						/>

						<Label for="showSystemMessage" class="cursor-pointer text-sm font-normal">
							Show system message in conversations
						</Label>
					</div>
				</div>
			{/if}
		{:else if field.type === SettingsFieldType.SELECT}
			{@const selectedOption = field.options?.find(
				(opt: { value: string; label: string; icon?: Component }) =>
					opt.value === localConfig[field.key]
			)}
			{@const currentValue = localConfig[field.key]}
			{@const serverDefault = currentModelParams[field.key]}
			{@const isCustomRealTime = (() => {
				if (serverDefault == null) return false;
				if (currentValue === '' || currentValue === undefined) return false;
				return currentValue !== serverDefault;
			})()}

			<div class="flex items-center gap-2">
				<Label for={field.key} class="flex items-center gap-1.5 text-sm font-medium">
					{field.label}

					{#if field.isExperimental}
						<FlaskConical class="h-3.5 w-3.5 text-muted-foreground" />
					{/if}
				</Label>
				{#if isCustomRealTime}
					<SettingsChatParameterSourceIndicator />
				{/if}
			</div>

			<Select.Root
				type="single"
				value={currentValue}
				onValueChange={(value) => {
					if (field.key === SETTINGS_KEYS.THEME && value && onThemeChange) {
						onThemeChange(value);
					} else {
						onConfigChange(field.key, value);
					}
				}}
			>
				<div class="relative w-full md:w-auto">
					<Select.Trigger class="w-full">
						<div class="flex items-center gap-2">
							{#if selectedOption?.icon}
								{@const IconComponent = selectedOption.icon}
								<IconComponent class="h-4 w-4" />
							{/if}

							{selectedOption?.label || `Select ${field.label.toLowerCase()}`}
						</div>
					</Select.Trigger>
					{#if isCustomRealTime}
						<button
							type="button"
							onclick={() => {
								settingsStore.resetParameterToServerDefault(field.key);
								onConfigChange(field.key, '');
							}}
							class="absolute top-1/2 right-8 inline-flex h-5 w-5 -translate-y-1/2 items-center justify-center rounded transition-colors hover:bg-muted"
							aria-label="Reset to default"
							title="Reset to default"
						>
							<RotateCcw class="h-3 w-3" />
						</button>
					{/if}
				</div>
				<Select.Content>
					{#if field.options}
						{#each field.options as option (option.value)}
							<Select.Item value={option.value} label={option.label}>
								<div class="flex items-center gap-2">
									{#if option.icon}
										{@const IconComponent = option.icon}
										<IconComponent class="h-4 w-4" />
									{/if}
									{option.label}
								</div>
							</Select.Item>
						{/each}
					{/if}
				</Select.Content>
			</Select.Root>
			{#if field.help || SETTING_CONFIG_INFO[field.key]}
				<p class="mt-1 text-xs text-muted-foreground">
					{field.help || SETTING_CONFIG_INFO[field.key]}
				</p>
			{/if}

			{#if field.key === SETTINGS_KEYS.THEME}
				<!-- Language Settings -->
				<div class="mt-4 space-y-2">
					<Label class="flex items-center gap-1.5 text-sm font-medium">
						<Globe class="h-4 w-4 text-primary" />
						{t('settings.language', 'Language')}
						<span class="text-[10px] px-1.5 py-0.2 rounded bg-amber-500/15 text-amber-500 border border-amber-500/30 font-mono font-normal">Advanced</span>
					</Label>
					<Select.Root
						type="single"
						value={i18n.currentLocale}
						onValueChange={(value) => {
							if (value === 'en' || value === 'ja') {
								i18n.setLocale(value);
							}
						}}
					>
						<Select.Trigger class="w-full">
							<div class="flex items-center gap-2">
								<Globe class="h-4 w-4 text-muted-foreground" />
								{SUPPORTED_LOCALES.find(l => l.code === i18n.currentLocale)?.nativeLabel || 'Language'}
							</div>
						</Select.Trigger>
						<Select.Content>
							{#each SUPPORTED_LOCALES as locale (locale.code)}
								<Select.Item value={locale.code} label={locale.nativeLabel}>
									<div class="flex items-center justify-between w-full">
										<span>{locale.nativeLabel}</span>
										<span class="text-xs text-muted-foreground ml-2">({locale.label})</span>
									</div>
								</Select.Item>
							{/each}
						</Select.Content>
					</Select.Root>
					<p class="text-xs text-muted-foreground">
						{t('settings.languageDesc', 'Select your preferred interface language.')}
					</p>
				</div>

				<!-- Theme Color (Hue) -->
				<div class="mt-4 rounded-xl border border-border/50 bg-card/40 p-4 space-y-3">
					<div class="flex items-center justify-between">
						<Label class="flex items-center gap-2 text-sm font-medium">
							<Palette class="h-4 w-4 text-primary" />
							{t('settings.themeColor', 'Theme Color (Hue)')}
						</Label>
						<div class="flex items-center gap-2">
							<span class="text-xs font-mono text-muted-foreground">{themeHue}°</span>
							<button
								type="button"
								onclick={handleResetHue}
								class="text-xs text-primary hover:underline"
							>
								{t('settings.reset', 'Reset')}
							</button>
						</div>
					</div>
					<input
						type="range"
						min="0"
						max="360"
						bind:value={themeHue}
						class="w-full h-2 rounded-lg appearance-none cursor-pointer"
						style="background: linear-gradient(to right, #ff0000 0%, #ffff00 17%, #00ff00 33%, #00ffff 50%, #0000ff 67%, #ff00ff 83%, #ff0000 100%)"
					/>
					<p class="text-xs text-muted-foreground">
						{t('settings.themeColorDesc', 'Customize base accent hue from 0° to 360°.')}
					</p>
				</div>
			{/if}
		{:else if field.type === SettingsFieldType.CHECKBOX}
			<div class="flex items-start space-x-3">
				<Checkbox
					id={field.key}
					checked={Boolean(localConfig[field.key])}
					onCheckedChange={(checked) => onConfigChange(field.key, checked === true)}
					class="mt-1"
				/>

				<div class="space-y-1">
					<label
						for={field.key}
						class="flex cursor-pointer items-center gap-1.5 pt-1 pb-0.5 text-sm leading-none font-medium"
					>
						{field.key === SETTINGS_KEYS.AUTO_SUMMARIZE ? t('settings.autoSummarize', field.label) : field.label}

						{#if field.isExperimental}
							<FlaskConical class="h-3.5 w-3.5 text-muted-foreground" />
						{/if}
					</label>

					{#if field.help || SETTING_CONFIG_INFO[field.key]}
						<p class="text-xs text-muted-foreground">
							{field.key === SETTINGS_KEYS.AUTO_SUMMARIZE ? t('settings.autoSummarizeHelp', field.help || SETTING_CONFIG_INFO[field.key]) : (field.help || SETTING_CONFIG_INFO[field.key])}
						</p>
					{/if}
				</div>
			</div>
		{/if}
	</div>
{/each}

<DialogConfirmation
	open={showDisableBaseRulesDialog}
	title="出力基盤ルールの無効化"
	description="出力基盤ルール（ト書き記法）を無効化すると、Live2Dアバターの表情同期やTTS音声のセリフ抽出が正常に動作しなくなる可能性があります。プレーンなプロンプトなし対話や実験用途以外では推奨されません。本当に無効化しますか？"
	confirmText="無効化する"
	cancelText="キャンセル"
	variant="destructive"
	icon={AlertTriangle}
	onConfirm={handleConfirmDisableBaseRules}
	onCancel={handleCancelDisableBaseRules}
/>

