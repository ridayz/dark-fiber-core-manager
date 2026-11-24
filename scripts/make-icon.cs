// make-icon.cs - gambar ikon Dark Fiber Core Manager, pack jadi .ico multi-size.
// Ikon: plat gelap NOC + 12 titik warna fiber TIA-598 (urutan tube 12 core) + inti amber.
// Build (no NuGet, .NET Framework csc yang sudah ada di Windows):
//   csc /nologo /target:exe /optimize+ /reference:System.Drawing.dll /out:make-icon.exe scripts\make-icon.cs
// Run:
//   make-icon.exe assets\icon.ico
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;

static class MakeFiberIcon
{
    static readonly int[] Sizes = { 16, 24, 32, 48, 64, 128, 256 };

    // Urutan warna fiber TIA-598 (12 core per tube), sama dengan peta core di app.
    static readonly string[] FiberColors =
    {
        "#3b82f6", "#f97316", "#22c55e", "#a16207",
        "#64748b", "#e2e8f0", "#ef4444", "#334155",
        "#eab308", "#8b5cf6", "#f43f5e", "#06b6d4",
    };

    static readonly Color PlateTop = ColorTranslator.FromHtml("#1b2836");
    static readonly Color PlateBottom = ColorTranslator.FromHtml("#05080c");
    static readonly Color Amber = ColorTranslator.FromHtml("#e3a94f");
    static readonly Color Ink = ColorTranslator.FromHtml("#0a1017");

    [STAThread]
    static int Main(string[] args)
    {
        string outPath = args.Length > 0 ? args[0] : "icon.ico";
        var payloads = new List<byte[]>();
        foreach (int size in Sizes)
        {
            using (Bitmap art = Draw(size))
            {
                // 256 disimpan sebagai PNG (konvensi Windows, hemat ~240 KB); sisanya DIB.
                payloads.Add(size >= 256 ? ToPng(art) : ToDib(art));
            }
        }

        WriteIco(outPath, payloads);
        Console.WriteLine("wrote " + outPath + " (" + payloads.Count + " sizes: " + string.Join(",", Sizes) + ")");
        return 0;
    }

    static Bitmap Draw(int size)
    {
        float s = size;
        var art = new Bitmap(size, size, PixelFormat.Format32bppArgb);
        using (Graphics g = Graphics.FromImage(art))
        {
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.PixelOffsetMode = PixelOffsetMode.HighQuality;
            g.Clear(Color.Transparent);

            // Plat rounded-square dengan gradasi halus, plus garis tepi tipis.
            using (GraphicsPath plate = Rounded(0.02f * s, 0.02f * s, 0.96f * s, 0.96f * s, 0.22f * s))
            {
                using (var fill = new LinearGradientBrush(new RectangleF(0, 0, s, s), PlateTop, PlateBottom, 90f))
                {
                    g.FillPath(fill, plate);
                }
                using (var edge = new Pen(Color.FromArgb(70, Amber), Math.Max(0.8f, 0.012f * s)))
                {
                    g.DrawPath(edge, plate);
                }
            }

            // Orbit amber tipis: cuma di ukuran besar, biar 16px tetap bersih.
            if (size >= 64)
            {
                float orbit = 0.46f * s;
                using (var pen = new Pen(Color.FromArgb(70, Amber), Math.Max(1f, 0.011f * s)))
                {
                    g.DrawEllipse(pen, s / 2f - orbit, s / 2f - orbit, orbit * 2, orbit * 2);
                }
            }

            // Inti: core fiber. Di ukuran kecil cukup satu titik amber, di besar cincin.
            if (size >= 48)
            {
                float core = 0.145f * s;
                using (var fill = new SolidBrush(Ink))
                {
                    g.FillEllipse(fill, s / 2f - core, s / 2f - core, core * 2, core * 2);
                }
                using (var pen = new Pen(Color.FromArgb(225, Amber), Math.Max(1f, 0.015f * s)))
                {
                    g.DrawEllipse(pen, s / 2f - core, s / 2f - core, core * 2, core * 2);
                }
            }
            else
            {
                float core = Math.Max(0.072f * s, 1.3f);
                using (var fill = new SolidBrush(Amber))
                {
                    g.FillEllipse(fill, s / 2f - core, s / 2f - core, core * 2, core * 2);
                }
            }

            // 12 core mengelilingi inti. Di 16px cukup 6 core, biar nggak jadi blob.
            float ring = (size < 24 ? 0.32f : 0.335f) * s;
            float dot = Math.Max((size < 24 ? 0.095f : 0.062f) * s, 1.1f);
            int step = size < 24 ? 2 : 1;
            for (int i = 0; i < FiberColors.Length; i += step)
            {
                double angle = -Math.PI / 2 + i * (Math.PI / 6);
                float cx = s / 2f + (float)(Math.Cos(angle) * ring);
                float cy = s / 2f + (float)(Math.Sin(angle) * ring);
                using (var brush = new SolidBrush(ColorTranslator.FromHtml(FiberColors[i])))
                {
                    g.FillEllipse(brush, cx - dot, cy - dot, dot * 2, dot * 2);
                }
            }
        }
        return art;
    }

    static GraphicsPath Rounded(float x, float y, float w, float h, float r)
    {
        var p = new GraphicsPath();
        p.AddArc(x, y, 2 * r, 2 * r, 180, 90);
        p.AddArc(x + w - 2 * r, y, 2 * r, 2 * r, 270, 90);
        p.AddArc(x + w - 2 * r, y + h - 2 * r, 2 * r, 2 * r, 0, 90);
        p.AddArc(x, y + h - 2 * r, 2 * r, 2 * r, 90, 90);
        p.CloseFigure();
        return p;
    }

    static byte[] ToPng(Bitmap bmp)
    {
        using (var ms = new MemoryStream())
        {
            bmp.Save(ms, ImageFormat.Png);
            return ms.ToArray();
        }
    }

    // Frame .ico wajib DIB (BMP) 32-bit bottom-up + AND mask.
    static byte[] ToDib(Bitmap bmp)
    {
        int s = bmp.Width;
        BitmapData data = bmp.LockBits(new Rectangle(0, 0, s, s), ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
        byte[] pixels = new byte[data.Stride * s];
        Marshal.Copy(data.Scan0, pixels, 0, pixels.Length);
        bmp.UnlockBits(data);

        var ms = new MemoryStream();
        using (var bw = new BinaryWriter(ms))
        {
            bw.Write(40);              // biSize
            bw.Write(s);               // biWidth
            bw.Write(s * 2);           // biHeight (XOR + AND)
            bw.Write((short)1);        // biPlanes
            bw.Write((short)32);       // biBitCount
            bw.Write(0);               // biCompression = BI_RGB
            bw.Write(s * s * 4);       // biSizeImage
            bw.Write(0);               // biXPelsPerMeter
            bw.Write(0);               // biYPelsPerMeter
            bw.Write(0);               // biClrUsed
            bw.Write(0);               // biClrImportant

            for (int y = s - 1; y >= 0; y--)
            {
                bw.Write(pixels, y * data.Stride, s * 4);
            }

            int maskRow = ((s + 31) / 32) * 4;
            bw.Write(new byte[maskRow * s]); // alpha di pixel yang menentukan transparansi
        }
        return ms.ToArray();
    }

    static void WriteIco(string path, List<byte[]> payloads)
    {
        string dir = Path.GetDirectoryName(Path.GetFullPath(path));
        if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);

        using (var fs = File.Create(path))
        using (var bw = new BinaryWriter(fs))
        {
            bw.Write((short)0);                  // reserved
            bw.Write((short)1);                  // type = icon
            bw.Write((short)payloads.Count);     // image count

            int offset = 6 + 16 * payloads.Count;
            for (int i = 0; i < payloads.Count; i++)
            {
                int size = Sizes[i];
                bw.Write((byte)(size >= 256 ? 0 : size)); // width
                bw.Write((byte)(size >= 256 ? 0 : size)); // height
                bw.Write((byte)0);               // palette count
                bw.Write((byte)0);               // reserved
                bw.Write((short)1);              // planes
                bw.Write((short)32);             // bit depth
                bw.Write(payloads[i].Length);    // bytes in resource
                bw.Write(offset);                // image offset
                offset += payloads[i].Length;
            }

            foreach (byte[] payload in payloads)
            {
                bw.Write(payload);
            }
        }
    }
}
