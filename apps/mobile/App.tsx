/**
 * Pathyam mobile client.
 *
 * WHAT THIS REPLACED
 * ------------------
 * The previous App.tsx was a demo that fabricated all four of the product's core
 * claims, in the UI layer, after the same fabrications had been removed from the
 * engine:
 *
 *   1. A hardcoded "observed" plate (white rice at confidence 0.93, sambar at 0.88)
 *      presented as vision output. The engine's vision provider was rewritten to
 *      fail loudly rather than return exactly this; the app kept returning it.
 *   2. `setTimeout(1200)` followed by an alert reading "Meal photo analyzed!" —
 *      an analysis that never ran, reported as one that had.
 *   3. Calories computed **in the client** as
 *      `label.includes('rice') ? 130 : 65` grams per 100 g, wrapped in a ±22%/26%
 *      interval. A calorie figure derived from a substring match, shown with an
 *      uncertainty band, to a person managing type 2 diabetes. The architecture
 *      rule this breaks is the one the whole system rests on: the deterministic
 *      engine computes nutrients and the client never does.
 *   4. Two clinical claims stamped "Verified" by a badge that verified nothing,
 *      one of them carrying a real-looking PMID.
 *
 * None of it called the API. It could not have: it sent no Authorization header,
 * and every endpoint it named has required a bearer token since authentication
 * landed.
 *
 * WHAT THIS IS
 * ------------
 * A smaller app that actually talks to the server. Every number on screen came
 * from the engine; nothing is computed here.
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Platform,
  SafeAreaView,
  ScrollView,
  StatusBar,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';

import { ApiError, apiClient } from './services/apiClient';
import { CONSENT_CGM_TELEMETRY } from './services/config';
import { GlucoseSync, SyncResult, platformHealthStore } from './services/glucose';

type Tab = 'log' | 'glucose';

interface HistoryEntry {
  id: string;
  notes?: string;
  energy_kcal?: number | null;
  energy_low?: number | null;
  energy_high?: number | null;
  meal_type?: string;
  consumed_at?: string;
}

export default function App() {
  const [signedIn, setSignedIn] = useState(false);
  const [tab, setTab] = useState<Tab>('log');

  if (!signedIn) return <SignIn onSignedIn={() => setSignedIn(true)} />;

  return (
    <SafeAreaView style={styles.container}>
      <StatusBar barStyle="dark-content" backgroundColor="#F8FAFC" />
      <View style={styles.header}>
        <Text style={styles.headerTitle}>PATHYAM</Text>
        <Text style={styles.headerSubtitle}>Regional South Indian nutrition</Text>
      </View>

      <View style={styles.tabBar}>
        <TabButton label="Log" active={tab === 'log'} onPress={() => setTab('log')} />
        <TabButton label="Glucose" active={tab === 'glucose'} onPress={() => setTab('glucose')} />
      </View>

      <ScrollView style={styles.content}>
        {tab === 'log' ? <LogScreen /> : <GlucoseScreen />}
      </ScrollView>
    </SafeAreaView>
  );
}

function TabButton({ label, active, onPress }: { label: string; active: boolean; onPress: () => void }) {
  return (
    <TouchableOpacity style={[styles.tabButton, active && styles.activeTabButton]} onPress={onPress}>
      <Text style={[styles.tabText, active && styles.activeTabText]}>{label}</Text>
    </TouchableOpacity>
  );
}

/** Shows what actually went wrong, including the server's own message. */
function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <View style={styles.errorBox}>
      <Text style={styles.errorText}>{error}</Text>
    </View>
  );
}

function describe(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return String(error);
}

// ------------------------------------------------------------------- auth --

function SignIn({ onSignedIn }: { onSignedIn: () => void }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (mode: 'login' | 'register') => {
    setBusy(true);
    setError(null);
    try {
      if (mode === 'login') await apiClient.login(email.trim(), password);
      else await apiClient.register(email.trim(), password);
      onSignedIn();
    } catch (err) {
      setError(describe(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <SafeAreaView style={styles.container}>
      <StatusBar barStyle="dark-content" backgroundColor="#F8FAFC" />
      <View style={styles.header}>
        <Text style={styles.headerTitle}>PATHYAM</Text>
      </View>
      <View style={styles.card}>
        <Text style={styles.cardTitle}>Sign in</Text>
        <TextInput
          style={styles.input}
          placeholder="email"
          placeholderTextColor="#64748B"
          autoCapitalize="none"
          keyboardType="email-address"
          value={email}
          onChangeText={setEmail}
        />
        <TextInput
          style={styles.input}
          placeholder="password"
          placeholderTextColor="#64748B"
          secureTextEntry
          value={password}
          onChangeText={setPassword}
        />
        <ErrorNote error={error} />
        <TouchableOpacity style={styles.actionButton} disabled={busy} onPress={() => submit('login')}>
          {busy ? <ActivityIndicator color="#FFF" /> : <Text style={styles.actionButtonText}>Sign in</Text>}
        </TouchableOpacity>
        <TouchableOpacity style={styles.secondaryButton} disabled={busy} onPress={() => submit('register')}>
          <Text style={styles.secondaryButtonText}>Create an account</Text>
        </TouchableOpacity>
        <Text style={styles.note}>
          There is no password reset yet. An account whose password is lost cannot be recovered.
        </Text>
      </View>
    </SafeAreaView>
  );
}

// -------------------------------------------------------------------- log --

function LogScreen() {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [entries, setEntries] = useState<HistoryEntry[]>([]);

  const refresh = useCallback(async () => {
    try {
      const history = await apiClient.history();
      setEntries(history.entries as HistoryEntry[]);
    } catch (err) {
      setError(describe(err));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const submit = async () => {
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await apiClient.logMeal(text.trim());
      setText('');
      await refresh();
    } catch (err) {
      setError(describe(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <View>
      <View style={styles.card}>
        <Text style={styles.cardTitle}>Log a meal</Text>
        <Text style={styles.cardDesc}>In your own words — "2 idli and sambar", "ஒரு தோசை".</Text>
        <TextInput
          style={styles.input}
          placeholder="what did you eat?"
          placeholderTextColor="#64748B"
          value={text}
          onChangeText={setText}
          onSubmitEditing={submit}
        />
        <ErrorNote error={error} />
        <TouchableOpacity style={styles.actionButton} disabled={busy} onPress={submit}>
          {busy ? <ActivityIndicator color="#FFF" /> : <Text style={styles.actionButtonText}>Log it</Text>}
        </TouchableOpacity>
      </View>

      <View style={styles.card}>
        <Text style={styles.cardTitle}>Recent</Text>
        {entries.length === 0 ? (
          <Text style={styles.note}>Nothing logged yet.</Text>
        ) : (
          entries.map((entry) => (
            <View key={entry.id} style={styles.entryRow}>
              <Text style={styles.entryTitle}>{entry.notes ?? '(no description)'}</Text>
              <Text style={styles.entryValue}>{formatEnergy(entry)}</Text>
            </View>
          ))
        )}
      </View>
    </View>
  );
}

/**
 * Render the engine's own interval. Never computed here, and never filled in when
 * the engine returned nothing: a meal that did not resolve has no energy, and the
 * previous journal used to substitute a flat 300 kcal in that case.
 */
function formatEnergy(entry: HistoryEntry): string {
  if (entry.energy_kcal === null || entry.energy_kcal === undefined) return 'not computed';
  const point = Math.round(entry.energy_kcal);
  const low = entry.energy_low;
  const high = entry.energy_high;
  if (low === null || low === undefined || high === null || high === undefined) {
    return `${point} kcal`;
  }
  return `${point} kcal (${Math.round(low)}–${Math.round(high)})`;
}

// ---------------------------------------------------------------- glucose --

function GlucoseScreen() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<SyncResult | null>(null);
  const [consented, setConsented] = useState(false);

  const grant = async () => {
    setError(null);
    try {
      const res = await apiClient.grantConsent(CONSENT_CGM_TELEMETRY);
      setConsented(res.granted);
    } catch (err) {
      setError(describe(err));
    }
  };

  const sync = async () => {
    setBusy(true);
    setError(null);
    try {
      const store = await platformHealthStore();
      // A 24-hour window with deliberate overlap on every run. A sensor that
      // reconnects backfills readings timestamped earlier than the last one seen,
      // so syncing strictly forward from the newest timestamp would drop exactly
      // the readings a connectivity gap produced. The server upserts, so overlap
      // costs nothing.
      const since = new Date(Date.now() - 24 * 60 * 60 * 1000);
      setResult(await new GlucoseSync(store, apiClient).sync(since));
    } catch (err) {
      setError(describe(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <View>
      <View style={styles.card}>
        <Text style={styles.cardTitle}>Glucose</Text>
        <Text style={styles.cardDesc}>
          Reads readings your CGM already writes to {platformStoreName()}. Works with any sensor
          that writes there, including FreeStyle Libre and Dexcom.
        </Text>
        <Text style={styles.note}>
          A continuous trace shows when you eat, sleep, exercise and are ill, so it is stored only
          if you grant it separately from the rest of the app.
        </Text>

        <TouchableOpacity style={styles.secondaryButton} onPress={grant}>
          <Text style={styles.secondaryButtonText}>
            {consented ? 'Consent granted' : 'Allow glucose storage'}
          </Text>
        </TouchableOpacity>

        <ErrorNote error={error} />

        <TouchableOpacity style={styles.actionButton} disabled={busy} onPress={sync}>
          {busy ? <ActivityIndicator color="#FFF" /> : <Text style={styles.actionButtonText}>Sync last 24 hours</Text>}
        </TouchableOpacity>

        {result && (
          <View style={styles.entryRow}>
            <Text style={styles.entryTitle}>
              {result.accepted} stored, {result.duplicates} already had
            </Text>
            <Text style={styles.entryValue}>
              {result.rejected > 0 ? `${result.rejected} out of range` : ''}
            </Text>
          </View>
        )}
      </View>

      <View style={styles.card}>
        <Text style={styles.cardTitle}>About the glucose curve</Text>
        <Text style={styles.note}>
          Pathyam does not predict your glucose response. The curve in the API is illustrative:
          its coefficients have never been fitted against measured readings. Storing yours is what
          would make fitting possible.
        </Text>
      </View>
    </View>
  );
}

function platformStoreName(): string {
  return Platform.OS === 'ios' ? 'Apple Health' : 'Health Connect';
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F8FAFC' },
  header: { paddingHorizontal: 20, paddingVertical: 16 },
  headerTitle: { color: '#0F172A', fontSize: 22, fontWeight: '700', letterSpacing: 2 },
  headerSubtitle: { color: '#64748B', fontSize: 13, marginTop: 2 },
  tabBar: { flexDirection: 'row', paddingHorizontal: 12, gap: 8 },
  tabButton: { paddingVertical: 8, paddingHorizontal: 16, borderRadius: 8, backgroundColor: '#E2E8F0' },
  activeTabButton: { backgroundColor: '#2563EB' },
  tabText: { color: '#475569', fontWeight: '600' },
  activeTabText: { color: '#FFFFFF' },
  content: { flex: 1, paddingHorizontal: 12, marginTop: 12 },
  card: {
    backgroundColor: '#FFFFFF', borderRadius: 12, padding: 16, marginBottom: 12,
    marginHorizontal: 8, borderWidth: 1, borderColor: '#E2E8F0',
  },
  cardTitle: { color: '#0F172A', fontSize: 17, fontWeight: '700', marginBottom: 4 },
  cardDesc: { color: '#475569', fontSize: 13, marginBottom: 12 },
  note: { color: '#64748B', fontSize: 12, marginTop: 10, lineHeight: 17 },
  input: {
    backgroundColor: '#F8FAFC', color: '#0F172A', borderRadius: 8,
    borderWidth: 1, borderColor: '#CBD5E1',
    paddingHorizontal: 12, paddingVertical: 10, marginBottom: 10, fontSize: 15,
  },
  actionButton: {
    backgroundColor: '#2563EB', borderRadius: 8, paddingVertical: 12,
    alignItems: 'center', marginTop: 6,
  },
  actionButtonText: { color: '#FFFFFF', fontWeight: '700', fontSize: 15 },
  secondaryButton: { paddingVertical: 10, alignItems: 'center' },
  secondaryButtonText: { color: '#2563EB', fontWeight: '600' },
  errorBox: {
    backgroundColor: '#FEF2F2', borderRadius: 8, padding: 10, marginBottom: 8,
    borderWidth: 1, borderColor: '#FECACA',
  },
  errorText: { color: '#991B1B', fontSize: 13 },
  entryRow: {
    flexDirection: 'row', justifyContent: 'space-between',
    paddingVertical: 8, borderTopWidth: 1, borderTopColor: '#E2E8F0',
  },
  entryTitle: { color: '#1E293B', fontSize: 14, flex: 1 },
  entryValue: { color: '#475569', fontSize: 13 },
});
