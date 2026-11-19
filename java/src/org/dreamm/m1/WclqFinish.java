package org.dreamm.m1;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.SeekableByteChannel;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.OpenOption;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardOpenOption;
import java.nio.file.attribute.BasicFileAttributes;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import uclv.camdbir.protdcal.descriptor.tools.DescriptorTools;

/** Owned, dependency-free bridge; original scientific JAR is never repackaged.
 * Compile --release 8. No Nashorn, JSON engine, subprocess, network or model API.
 * The existing admitted worker owns execution, limits, cleanup and publication.
 */
public final class WclqFinish {
    private static final int LIMIT = 8 * 1024 * 1024;
    private static final String SCHEMA_SHA = "3e58e613135995692a910aefca5abdbb3d4f9f5ebce042bc9ecd795cfab5fdbd";
    private static final String TABLE_SHA = "9d0a8dcc3dcb24a15ffec0336554fd2fbfe78f6ec28b0c46de74b48749d98d27";
    private WclqFinish() { }

    private static void require(boolean value, String message) {
        if (!value) throw new IllegalArgumentException(message);
    }

    private static byte[] read(Path path) throws IOException {
        BasicFileAttributes before = Files.readAttributes(path, BasicFileAttributes.class, LinkOption.NOFOLLOW_LINKS);
        require(before.isRegularFile() && !before.isSymbolicLink() && before.size() <= LIMIT, "input type/size");
        byte[] raw;
        try (SeekableByteChannel channel = Files.newByteChannel(path,
                new HashSet<OpenOption>(Arrays.asList(StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS)))) {
            require(channel.size() == before.size(), "input size changed");
            ByteBuffer buffer = ByteBuffer.allocate((int) before.size() + 1);
            while (buffer.hasRemaining() && channel.read(buffer) >= 0) { }
            require(buffer.position() == before.size(), "input changed while reading");
            raw = Arrays.copyOf(buffer.array(), buffer.position());
        }
        BasicFileAttributes after = Files.readAttributes(path, BasicFileAttributes.class, LinkOption.NOFOLLOW_LINKS);
        require(Objects.equals(before.fileKey(), after.fileKey()) && before.size() == after.size()
                && before.lastModifiedTime().equals(after.lastModifiedTime()), "input replaced");
        return raw;
    }

    private static String text(byte[] raw) throws CharacterCodingException {
        return StandardCharsets.US_ASCII.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(raw)).toString();
    }

    private static String hash(byte[] raw) throws Exception {
        byte[] value = MessageDigest.getInstance("SHA-256").digest(raw);
        StringBuilder out = new StringBuilder(64);
        for (byte b : value) out.append(String.format(java.util.Locale.ROOT, "%02x", b & 255));
        return out.toString();
    }

    private static String[] fields(String line) { return line.split("\\t", -1); }

    private static List<String> lines(byte[] raw) throws IOException {
        String value = text(raw);
        require(!value.contains("\r") && value.endsWith("\n"), "LF terminated artifact required");
        return Arrays.asList(value.substring(0, value.length() - 1).split("\n", -1));
    }

    public static void main(String[] args) throws Exception {
        require(args.length == 5, "fixed roles: source PDB, reduced AA, unrounded TSV, schema TSV, complete AA");
        byte[] source = read(Paths.get(args[0])), reduced = read(Paths.get(args[1]));
        byte[] pending = read(Paths.get(args[2])), schemaBytes = read(Paths.get(args[3]));
        require(hash(schemaBytes).equals(SCHEMA_SHA), "foreign schema fingerprint");
        List<String> schemaLines = lines(schemaBytes);
        require(schemaLines.size() == 1, "single schema header");
        List<String> schema = Arrays.asList(fields(schemaLines.get(0)));
        require(schema.size() == 500 && schema.get(0).equals("AA") && new HashSet<String>(schema).size() == 500, "499 schema");
        List<String> selected = new ArrayList<String>(), original = new ArrayList<String>();
        for (String name : schema) {
            if (name.startsWith("wCLQ(") && !name.equals("wCLQ(Ap)_NO")) selected.add(name);
            else original.add(name);
        }
        require(selected.size() == 29 && original.size() == 471, "29/470 column contract");
        List<String> unrounded = lines(pending);
        require(unrounded.size() >= 4 && unrounded.get(0).equals("#dreamm-wclq-unrounded/2"), "unrounded version");
        Map<String, String> metadata = new HashMap<String, String>();
        for (String field : fields(unrounded.get(1))) {
            String[] item = field.split("=", -1);
            require(item.length == 2 && metadata.put(item[0], item[1]) == null, "duplicate/malformed metadata");
        }
        require(metadata.size() == 4 && hash(source).equals(metadata.get("source_sha256"))
                && hash(reduced).equals(metadata.get("reduced_AA_sha256"))
                && SCHEMA_SHA.equals(metadata.get("schema_sha256")) && TABLE_SHA.equals(metadata.get("tables_sha256")), "foreign input receipt");
        List<String> wanted = new ArrayList<String>(); wanted.add("AA"); wanted.addAll(selected);
        require(Arrays.asList(fields(unrounded.get(2))).equals(wanted), "29-column order");
        Map<String, String[]> values = new LinkedHashMap<String, String[]>();
        for (int i = 3; i < unrounded.size(); i++) {
            String[] row = fields(unrounded.get(i));
            require(row.length == 30 && row[0].matches("[A-Za-z0-9_.-]+_[A-Za-z0-9]_[A-Z]{3}_-?[0-9]+")
                    && values.put(row[0], row) == null && values.size() <= 5000, "unrounded identity/width/bound");
            for (int j = 1; j < row.length; j++) {
                require(row[j].matches("[-+]?0x[0-9a-f]+\\.[0-9a-f]+p[-+][0-9]+"), "exact double hex required");
                double value = Double.parseDouble(row[j]);
                require(!Double.isNaN(value) && !Double.isInfinite(value), "nonfinite value");
            }
        }
        List<String> aa = lines(reduced);
        require(aa.size() >= 4 && aa.get(0).contains("PARAMETERS") && aa.get(1).contains("t_cont:")
                && Arrays.asList(fields(aa.get(2))).equals(original), "470-column reduced artifact");
        Map<String, Integer> originalIndex = new HashMap<String, Integer>(), selectedIndex = new HashMap<String, Integer>();
        for (int i = 0; i < original.size(); i++) originalIndex.put(original.get(i), i);
        for (int i = 0; i < selected.size(); i++) selectedIndex.put(selected.get(i), i + 1);
        StringBuilder output = new StringBuilder();
        output.append(aa.get(0)).append('\n').append(aa.get(1)).append('\n').append(schemaLines.get(0)).append('\n');
        Set<String> seen = new HashSet<String>();
        for (int i = 3; i < aa.size(); i++) {
            String[] row = fields(aa.get(i));
            require(row.length == original.size() && seen.add(row[0]) && values.containsKey(row[0]), "reduced identity/width");
            String[] calculated = values.get(row[0]);
            for (int j = 0; j < schema.size(); j++) {
                if (j != 0) output.append('\t');
                String column = schema.get(j);
                if (selectedIndex.containsKey(column)) {
                    // Keep the exact pinned legacy serializer, including ties/sentinel behavior.
                    output.append(DescriptorTools.roundNumber(Double.parseDouble(calculated[selectedIndex.get(column)]), 3));
                } else output.append(row[originalIndex.get(column)]);
            }
            output.append('\n'); require(output.length() <= LIMIT, "complete output bound");
        }
        require(!seen.isEmpty() && seen.equals(values.keySet()), "missing/extra unrounded identities");
        byte[] raw = output.toString().getBytes(StandardCharsets.US_ASCII);
        Path target = Paths.get(args[4]);
        try (SeekableByteChannel channel = Files.newByteChannel(target,
                new HashSet<OpenOption>(Arrays.asList(StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE, LinkOption.NOFOLLOW_LINKS)),
                PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")))) {
            ByteBuffer buffer = ByteBuffer.wrap(raw); while (buffer.hasRemaining()) channel.write(buffer);
        }
        System.out.println("{\"schema\":\"dreamm-wclq-compiled/1\",\"rows\":" + seen.size()
                + ",\"columns\":499,\"complete_AA_sha256\":\"" + hash(raw) + "\",\"Nashorn\":false}");
    }
}
