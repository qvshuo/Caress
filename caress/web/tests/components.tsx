import { useState } from "react";
import { createRoot } from "react-dom/client";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogTrigger, DialogContent, DialogTitle, DialogDescription, DialogClose } from "@/components/ui/dialog";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";

function Fixture() {
  const [open, setOpen] = useState(false);
  const [choice, setChoice] = useState<string | null>("one");
  const [checked, setChecked] = useState(false);
  return <div className="p-8">
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button />}>打开受控弹窗</DialogTrigger>
      <DialogContent>
        <DialogTitle>受控弹窗</DialogTitle><DialogDescription>验证键盘与嵌套交互</DialogDescription>
        <input aria-label="弹窗输入" />
        <Select value={choice} onValueChange={setChoice} items={{ one: "第一项", two: "第二项" }}>
          <SelectTrigger aria-label="受控选择"><SelectValue /></SelectTrigger>
          <SelectContent><SelectItem value="one">第一项</SelectItem><SelectItem value="two">第二项</SelectItem></SelectContent>
        </Select>
        <Dialog><DialogTrigger render={<Button />}>打开嵌套弹窗</DialogTrigger><DialogContent><DialogTitle>嵌套弹窗</DialogTitle><DialogDescription>嵌套内容</DialogDescription><DialogClose render={<Button />}>关闭内层</DialogClose></DialogContent></Dialog>
        <DialogClose render={<Button />}>关闭外层</DialogClose>
      </DialogContent>
    </Dialog>
    <Dialog><DialogTrigger render={<Button />}>打开非受控弹窗</DialogTrigger><DialogContent><DialogTitle>非受控弹窗</DialogTitle><DialogDescription>非受控内容</DialogDescription></DialogContent></Dialog>
    <Select defaultValue="one" items={{one:"第一项",two:"第二项"}}><SelectTrigger aria-label="非受控选择"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="one">第一项</SelectItem><SelectItem value="two">第二项</SelectItem></SelectContent></Select>
    <Checkbox aria-label="受控复选" checked={checked} onCheckedChange={setChecked} />
    <Checkbox aria-label="非受控复选" defaultChecked />
    <Checkbox aria-label="部分选择" indeterminate />
    <Checkbox aria-label="禁用复选" disabled />
    <output>{choice}</output>
  </div>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
