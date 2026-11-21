import java.awt.*;
import java.awt.event.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import javax.swing.*;
import javax.swing.table.DefaultTableModel;

/** PC-Control test app: Swing + AWT controls. Every event is appended to the file given as the first argument, so a test can verify
 *  what really happened inside Java without trusting the accessibility bridge it is testing. */
public class PccSwingTest {
    static PrintWriter log;
    static JLabel status;

    static void ev(String s) {
        status.setText(s);
        log.println(s);
        log.flush();
    }

    public static void main(String[] args) throws Exception {
        log = new PrintWriter(new OutputStreamWriter(new FileOutputStream(args.length > 0 ? args[0] : "pcc-java-events.txt", true), StandardCharsets.UTF_8));
        SwingUtilities.invokeAndWait(() -> {
            JFrame f = new JFrame(args.length > 1 ? args[1] : "PCC Swing Test");
            f.setDefaultCloseOperation(JFrame.EXIT_ON_CLOSE);
            JMenuBar mb = new JMenuBar();
            JMenu file = new JMenu("Archivo");
            JMenuItem nuevo = new JMenuItem("Nuevo documento");
            nuevo.addActionListener(e -> ev("menu:Nuevo documento"));
            file.add(nuevo);
            mb.add(file);
            f.setJMenuBar(mb);

            JTabbedPane tabs = new JTabbedPane();
            JPanel general = new JPanel(new GridLayout(0, 2, 6, 6));
            JLabel nl = new JLabel("Nombre del cliente");
            JTextField name = new JTextField(18);
            nl.setLabelFor(name);
            name.getDocument().addDocumentListener(new javax.swing.event.DocumentListener() {
                public void insertUpdate(javax.swing.event.DocumentEvent e) { ev("text:" + name.getText()); }
                public void removeUpdate(javax.swing.event.DocumentEvent e) { }
                public void changedUpdate(javax.swing.event.DocumentEvent e) { }
            });
            JButton greet = new JButton("Saludar");
            greet.addActionListener(e -> ev("button:Saludar:" + name.getText()));
            JCheckBox accept = new JCheckBox("Acepto las condiciones");
            accept.addItemListener(e -> ev("check:" + accept.isSelected()));
            JComboBox<String> color = new JComboBox<>(new String[]{"Rojo", "Verde", "Azul"});
            color.getAccessibleContext().setAccessibleName("Color favorito");
            color.addActionListener(e -> ev("combo:" + color.getSelectedItem()));
            JButton modal = new JButton("Abrir dialogo");
            modal.addActionListener(e -> { ev("modal:open"); JOptionPane.showMessageDialog(f, "Dialogo modal de prueba", "Aviso PCC", JOptionPane.INFORMATION_MESSAGE); ev("modal:closed"); });
            general.add(nl); general.add(name);
            general.add(greet); general.add(accept);
            general.add(new JLabel("Color")); general.add(color);
            general.add(modal);
            status = new JLabel("listo");
            general.add(status);
            tabs.addTab("General", general);

            JPanel adv = new JPanel(new BorderLayout());
            DefaultTableModel m = new DefaultTableModel(new Object[][]{{"Pedido 1001", "Pendiente"}, {"Pedido 1002", "Enviado"}, {"Pedido 1003", "Cobrado"}},
                    new Object[]{"Pedido", "Estado"});
            JTable t = new JTable(m);
            adv.add(new JScrollPane(t), BorderLayout.CENTER);
            tabs.addTab("Avanzado", adv);
            tabs.addChangeListener(e -> ev("tab:" + tabs.getTitleAt(tabs.getSelectedIndex())));

            // heavyweight AWT controls: their own native windows inside the frame
            Panel awt = new Panel(new FlowLayout());
            java.awt.Button ab = new java.awt.Button("Boton AWT");
            ab.addActionListener(e -> ev("awt:button"));
            TextField at = new TextField("", 14);
            at.addTextListener(e -> ev("awt:text:" + at.getText()));
            awt.add(ab); awt.add(at);

            f.getContentPane().add(tabs, BorderLayout.CENTER);
            f.getContentPane().add(awt, BorderLayout.SOUTH);
            f.setSize(620, 420);
            f.setLocation(80, 80);
            f.setFocusableWindowState(true);
            f.setVisible(true);
            ev("ready");
        });
    }
}
